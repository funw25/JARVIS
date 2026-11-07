"""Bounded, local-only Jarvis task search over synthetic data."""
import base64
import binascii
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, validate_context
from .todo_service import _normalize_due

_STATUSES = {"open", "in_progress", "done", "cancelled"}
_PRIORITIES = {"low", "normal", "high"}


def _serialize(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _cursor(value: dict) -> str:
    return base64.urlsafe_b64encode(_serialize(value)).decode("ascii").rstrip("=")


def _range_boundary(value: str | None) -> tuple[datetime, str] | None:
    """Round a boundary upward because task due times are stored as whole seconds."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Invalid due range boundary")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("Due range must include a timezone")
        parsed = parsed.astimezone(timezone.utc)
        rounded = parsed.replace(microsecond=0) + (timedelta(seconds=1) if parsed.microsecond else timedelta())
        return parsed, rounded.isoformat(timespec="seconds").replace("+00:00", "Z")
    except (ValueError, OverflowError) as exc:
        raise ValueError("Invalid due range boundary") from exc


def _parse_cursor(value: str, fingerprint: str) -> tuple[int, str, str]:
    if not isinstance(value, str) or not value or len(value) > 1024 or not value.isascii():
        raise ValueError("Invalid task cursor")
    try:
        data = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Invalid task cursor") from exc
    if not isinstance(data, dict) or set(data) != {"v", "filter", "missing_due", "due", "id"}:
        raise ValueError("Invalid task cursor")
    if type(data["v"]) is not int or data["v"] != 1 or data["filter"] != fingerprint:
        raise ValueError("Task cursor does not match the query")
    missing, due, item_id = data["missing_due"], data["due"], data["id"]
    if type(missing) is not int or missing not in (0, 1) or not isinstance(due, str) or not isinstance(item_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item_id) is None:
        raise ValueError("Invalid task cursor")
    if (missing == 1 and due != "") or (missing == 0 and (not due or _normalize_due(due) != due)):
        raise ValueError("Invalid task cursor")
    return missing, due, item_id


class JarvisTaskQueryService:
    """Search owned Jarvis tasks with a stable order and bounded keyset pages.

    Cursors describe a position and filters, not a snapshot or authorization.
    Concurrent edits may cause results to move between pages.
    """

    def search(self, context: AssistantContext, *, query: str = "", statuses: tuple[str, ...] = ("open", "in_progress"),
               priority: str | None = None, due_from: str | None = None, due_before: str | None = None,
               limit: int = 25, cursor: str | None = None) -> dict:
        validate_context(context)
        if context.assistant_id != "jarvis":
            raise AccessDenied("Task search is available only to Jarvis")
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError("Task query must be at most 200 characters")
        query = query.strip()
        if not isinstance(statuses, (tuple, list)) or not statuses or len(statuses) > len(_STATUSES) or any(not isinstance(x, str) or x not in _STATUSES for x in statuses) or len(set(statuses)) != len(statuses):
            raise ValueError("Invalid task statuses")
        statuses = tuple(sorted(statuses))
        if priority is not None and (not isinstance(priority, str) or priority not in _PRIORITIES):
            raise ValueError("Invalid task priority")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("Task page size must be between 1 and 100")
        start_info, end_info = _range_boundary(due_from), _range_boundary(due_before)
        if start_info is not None and end_info is not None and start_info[0] >= end_info[0]:
            raise ValueError("Due range must be increasing")
        start = start_info[1] if start_info else None
        end = end_info[1] if end_info else None
        filters = {"owner": context.owner_id, "query": query, "statuses": statuses, "priority": priority,
                   "due_from": start, "due_before": end}
        fingerprint = hashlib.sha256(_serialize(filters)).hexdigest()
        after = _parse_cursor(cursor, fingerprint) if cursor is not None else None
        clauses = ["owner_id=?", "created_by_assistant='jarvis'", "deleted_at IS NULL",
                   "status IN (" + ",".join("?" for _ in statuses) + ")"]
        params = [context.owner_id, *statuses]
        if query:
            clauses.append("(instr(lower(title),lower(?))>0 OR instr(lower(description),lower(?))>0)")
            params.extend((query, query))
        if priority is not None:
            clauses.append("priority=?")
            params.append(priority)
        if start is not None:
            clauses.append("due_at>=?")
            params.append(start)
        if end is not None:
            clauses.append("due_at<?")
            params.append(end)
        if after is not None:
            clauses.append("((due_at IS NULL), COALESCE(due_at,''), id) > (?, ?, ?)")
            params.extend(after)
        params.append(limit + 1)
        sql = ("SELECT id,title,status,priority,due_at,created_at,updated_at FROM todos WHERE " + " AND ".join(clauses) +
               " ORDER BY (due_at IS NULL), COALESCE(due_at,''), id LIMIT ?")
        with connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        page, more = rows[:limit], len(rows) > limit
        next_cursor = None
        if more:
            last = page[-1]
            next_cursor = _cursor({"v": 1, "filter": fingerprint, "missing_due": int(last["due_at"] is None),
                                   "due": last["due_at"] or "", "id": last["id"]})
        return {"items": [dict(row) for row in page], "next_cursor": next_cursor}
