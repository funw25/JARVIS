"""Bounded local synthetic mailbox listing; no account or network access."""
import base64
import binascii
import hashlib
import json
import re
from datetime import datetime

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, validate_context

_STATUSES = {"new", "needs_reply", "waiting", "archived"}
_PRIORITIES = {"low", "normal", "high"}
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z\Z")


def _pack(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _read_cursor(value: str, fingerprint: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value or len(value) > 1024 or not value.isascii():
        raise ValueError("Invalid mailbox cursor")
    try:
        data = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Invalid mailbox cursor") from exc
    if not isinstance(data, dict) or set(data) != {"v", "filter", "received_at", "id"}:
        raise ValueError("Invalid mailbox cursor")
    timestamp, item_id = data["received_at"], data["id"]
    if type(data["v"]) is not int or data["v"] != 1 or data["filter"] != fingerprint:
        raise ValueError("Mailbox cursor does not match query")
    if not isinstance(timestamp, str) or _DATE.fullmatch(timestamp) is None or not isinstance(item_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item_id) is None:
        raise ValueError("Invalid mailbox cursor")
    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid mailbox cursor") from exc
    return timestamp, item_id


class SyntheticMailQueryService:
    def search(self, context: AssistantContext, *, query: str = "", statuses: tuple[str, ...] = ("new", "needs_reply", "waiting"),
               priority: str | None = None, limit: int = 25, cursor: str | None = None) -> dict:
        validate_context(context)
        if context.assistant_id != "jarvis":
            raise AccessDenied("Mailbox listing is available only to Jarvis")
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError("Mailbox query must be at most 200 characters")
        query = query.strip()
        if not isinstance(statuses, (list, tuple)) or not statuses or len(statuses) > len(_STATUSES) or any(not isinstance(item, str) or item not in _STATUSES for item in statuses) or len(set(statuses)) != len(statuses):
            raise ValueError("Invalid mailbox statuses")
        statuses = tuple(sorted(statuses))
        if priority is not None and (not isinstance(priority, str) or priority not in _PRIORITIES):
            raise ValueError("Invalid mailbox priority")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Mailbox page size must be between 1 and 100")
        fingerprint = hashlib.sha256(_pack({"owner": context.owner_id, "query": query, "statuses": statuses, "priority": priority})).hexdigest()
        after = _read_cursor(cursor, fingerprint) if cursor is not None else None
        clauses = ["owner_id=?", "source_kind='synthetic'", "triage_status IN (" + ",".join("?" for _ in statuses) + ")"]
        params = [context.owner_id, *statuses]
        if query:
            clauses.append("(instr(lower(subject),lower(?))>0 OR instr(lower(sender),lower(?))>0)")
            params.extend((query, query))
        if priority is not None:
            clauses.append("priority=?")
            params.append(priority)
        if after is not None:
            clauses.append("(received_at<? OR (received_at=? AND id<?))")
            params.extend((after[0], after[0], after[1]))
        params.append(limit + 1)
        sql = ("SELECT id,sender,subject,triage_status,priority,received_at,updated_at FROM mail_items WHERE "
               + " AND ".join(clauses) + " ORDER BY received_at DESC,id DESC LIMIT ?")
        with connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        page, more = rows[:limit], len(rows) > limit
        next_cursor = None
        if more:
            last = page[-1]
            next_cursor = base64.urlsafe_b64encode(_pack({"v": 1, "filter": fingerprint, "received_at": last["received_at"], "id": last["id"]})).decode("ascii").rstrip("=")
        return {"items": [dict(row) for row in page], "next_cursor": next_cursor, "synthetic": True}
