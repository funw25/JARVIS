"""Paginated local fictional capture and note listing."""
import base64
import binascii
import hashlib
import json
import re
from datetime import datetime

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, validate_context
from .synthetic_knowledge_tag_service import _tag

_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _pack(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _cursor(value: str, fingerprint: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value or len(value) > 1024 or not value.isascii():
        raise ValueError("Invalid knowledge cursor")
    try:
        data = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Invalid knowledge cursor") from exc
    if not isinstance(data, dict) or set(data) != {"v", "filter", "updated_at", "id"}:
        raise ValueError("Invalid knowledge cursor")
    when, item_id = data["updated_at"], data["id"]
    if type(data["v"]) is not int or data["v"] != 1 or data["filter"] != fingerprint:
        raise ValueError("Knowledge cursor does not match query")
    if not isinstance(when, str) or _DATE.fullmatch(when) is None or not isinstance(item_id, str) or _ID.fullmatch(item_id) is None:
        raise ValueError("Invalid knowledge cursor")
    try:
        datetime.fromisoformat(when.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid knowledge cursor") from exc
    return when, item_id


class SyntheticKnowledgeQueryService:
    def search(self, context: AssistantContext, kind: str, *, query: str = "", tag: str | None = None,
               status: str | None = None, limit: int = 25, cursor: str | None = None) -> dict:
        validate_context(context)
        if context.assistant_id != "jarvis":
            raise AccessDenied("Knowledge listing is available only to Jarvis")
        if kind not in ("capture", "note"):
            raise ValueError("Unknown knowledge kind")
        if not isinstance(query, str) or len(query) > 200:
            raise ValueError("Knowledge query must be at most 200 characters")
        query = query.strip()
        key = _tag(tag)[1] if tag is not None else None
        if status is not None and (kind != "capture" or status not in ("inbox", "processed", "archived")):
            raise ValueError("Invalid capture status")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Knowledge page size must be between 1 and 100")
        fingerprint = hashlib.sha256(_pack({"owner": context.owner_id, "kind": kind, "query": query, "tag": key, "status": status})).hexdigest()
        after = _cursor(cursor, fingerprint) if cursor is not None else None
        table, content = ("capture_items", "content") if kind == "capture" else ("knowledge_items", "body")
        columns = "item.id,item.title,item.created_at,item.updated_at" + (",item.status" if kind == "capture" else "")
        clauses = ["item.owner_id=?", "item.deleted_at IS NULL"]
        params = [context.owner_id]
        if kind == "capture":
            clauses.append("item.source_kind='manual_synthetic'")
        if query:
            clauses.append(f"(instr(lower(item.title),lower(?))>0 OR instr(lower(item.{content}),lower(?))>0)")
            params.extend((query, query))
        if status is not None:
            clauses.append("item.status=?")
            params.append(status)
        if key is not None:
            clauses.append("EXISTS (SELECT 1 FROM synthetic_knowledge_tags t WHERE t.owner_id=item.owner_id AND t.object_type=? AND t.object_id=item.id AND t.tag_key=?)")
            params.extend((kind, key))
        if after is not None:
            clauses.append("(item.updated_at<? OR (item.updated_at=? AND item.id<?))")
            params.extend((after[0], after[0], after[1]))
        params.append(limit + 1)
        sql = f"SELECT {columns} FROM {table} item WHERE " + " AND ".join(clauses) + " ORDER BY item.updated_at DESC,item.id DESC LIMIT ?"
        with connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        page, more = rows[:limit], len(rows) > limit
        next_cursor = None
        if more:
            last = page[-1]
            next_cursor = base64.urlsafe_b64encode(_pack({"v": 1, "filter": fingerprint, "updated_at": last["updated_at"], "id": last["id"]})).decode("ascii").rstrip("=")
        return {"kind": kind, "items": [dict(row) for row in page], "next_cursor": next_cursor, "synthetic": True}
