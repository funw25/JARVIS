"""Bounded lists of fictional drafts and pending send proposals."""
import base64
import binascii
import hashlib
import json
import re
from datetime import datetime, timezone

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, validate_context

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z\Z")


def _pack(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _jarvis(context):
    validate_context(context)
    if context.assistant_id != "jarvis":
        raise AccessDenied("Mail queues are available only to Jarvis")


def _parse(value, fingerprint):
    if not isinstance(value, str) or not value or len(value) > 1024 or not value.isascii():
        raise ValueError("Invalid mail queue cursor")
    try:
        data = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError("Invalid mail queue cursor") from exc
    if not isinstance(data, dict) or set(data) != {"v", "filter", "time", "id"}:
        raise ValueError("Invalid mail queue cursor")
    when, item_id = data["time"], data["id"]
    if type(data["v"]) is not int or data["v"] != 1 or data["filter"] != fingerprint:
        raise ValueError("Mail queue cursor does not match query")
    if not isinstance(when, str) or _DATE.fullmatch(when) is None or not isinstance(item_id, str) or _ID.fullmatch(item_id) is None:
        raise ValueError("Invalid mail queue cursor")
    try:
        datetime.fromisoformat(when.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid mail queue cursor") from exc
    return when, item_id


def _limit(limit):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Mail queue limit must be from 1 to 100")


def _next(page, more, fingerprint, time_field):
    if not more:
        return None
    last = page[-1]
    data = {"v": 1, "filter": fingerprint, "time": last[time_field], "id": last["id"]}
    return base64.urlsafe_b64encode(_pack(data)).decode("ascii").rstrip("=")


class SyntheticMailQueueService:
    def drafts(self, context: AssistantContext, *, mail_id: str | None = None, limit: int = 25, cursor: str | None = None) -> dict:
        _jarvis(context)
        _limit(limit)
        if mail_id is not None and (not isinstance(mail_id, str) or _ID.fullmatch(mail_id) is None):
            raise ValueError("Invalid mail identifier")
        fingerprint = hashlib.sha256(_pack({"owner": context.owner_id, "kind": "drafts", "mail_id": mail_id})).hexdigest()
        after = _parse(cursor, fingerprint) if cursor is not None else None
        clauses = ["d.owner_id=?", "m.owner_id=d.owner_id", "m.source_kind='synthetic'"]
        params = [context.owner_id]
        if mail_id is not None:
            clauses.append("d.mail_id=?")
            params.append(mail_id)
        if after is not None:
            clauses.append("(d.updated_at<? OR (d.updated_at=? AND d.id<?))")
            params.extend((after[0], after[0], after[1]))
        params.append(limit + 1)
        sql = ("SELECT d.id,d.mail_id,d.subject,d.version,d.created_at,d.updated_at FROM mail_drafts d JOIN mail_items m ON m.id=d.mail_id WHERE " +
               " AND ".join(clauses) + " ORDER BY d.updated_at DESC,d.id DESC LIMIT ?")
        with connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        page = rows[:limit]
        return {"items": [dict(row) for row in page], "next_cursor": _next(page, len(rows) > limit, fingerprint, "updated_at"), "synthetic": True}

    def pending_proposals(self, context: AssistantContext, *, limit: int = 25, cursor: str | None = None) -> dict:
        _jarvis(context)
        _limit(limit)
        fingerprint = hashlib.sha256(_pack({"owner": context.owner_id, "kind": "pending_proposals"})).hexdigest()
        after = _parse(cursor, fingerprint) if cursor is not None else None
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        clauses = ["p.owner_id=?", "p.kind='synthetic_email_send'", "p.status='pending'", "p.expires_at>?"]
        params = [context.owner_id, now]
        if after is not None:
            clauses.append("(p.expires_at>? OR (p.expires_at=? AND p.id>?))")
            params.extend((after[0], after[0], after[1]))
        params.append(limit + 1)
        sql = ("SELECT p.id,p.target_ref AS draft_id,p.expires_at,p.created_at FROM action_proposals p WHERE " +
               " AND ".join(clauses) + " ORDER BY p.expires_at,p.id LIMIT ?")
        with connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        page = rows[:limit]
        return {"items": [dict(row) for row in page], "next_cursor": _next(page, len(rows) > limit, fingerprint, "expires_at"), "synthetic": True, "execution_available": False}
