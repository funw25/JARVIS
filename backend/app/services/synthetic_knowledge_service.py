"""Jarvis-only local capture and knowledge services for synthetic data."""
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import uuid4

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, NotFound, validate_context


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _jarvis(context: AssistantContext) -> None:
    validate_context(context)
    if context.assistant_id != "jarvis":
        raise AccessDenied("This operation is available only to Jarvis")


def _text(value: str, label: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"Invalid {label}")
    return value


def _synthetic_url(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Invalid synthetic source URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid synthetic source URL") from exc
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
        or not parsed.hostname.lower().endswith(".invalid") or parsed.username is not None
        or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError("Synthetic source URLs must use an .invalid host without credentials, query, or fragment")
    return value


def _audit(conn, context: AssistantContext, event: str, object_type: str, object_id: str, now: str) -> None:
    conn.execute("INSERT INTO audit_events(id,owner_id,actor_type,actor_id,event_type,object_type,object_id,metadata_json,created_at) VALUES (?,?,'user',?,?,?,?,'{}',?)",
                 (str(uuid4()), context.owner_id, context.owner_id, event, object_type, object_id, now))


class SyntheticKnowledgeService:
    """Local synthetic capture inbox and notes; no fetching, model, or external account."""

    def create_capture(self, context: AssistantContext, title: str, content: str = "", source_url: str | None = None,
                       *, confirmed_by_user: bool = False) -> str:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required to save a capture")
        title = _text(title, "title", 500, required=True).strip()
        content = _text(content, "content", 100000)
        source_url = _synthetic_url(source_url)
        if not content.strip() and source_url is None:
            raise ValueError("A capture needs text or a synthetic URL")
        item_id, now = str(uuid4()), _now()
        with connect() as conn:
            conn.execute("INSERT INTO capture_items(id,owner_id,source_kind,title,content,source_url,status,created_at,updated_at,deleted_at) VALUES (?,?,'manual_synthetic',?,?,?,'inbox',?,?,NULL)",
                         (item_id, context.owner_id, title, content, source_url, now, now))
            _audit(conn, context, "capture.created", "capture", item_id, now)
        return item_id

    def list_captures(self, context: AssistantContext, query: str = "", status: str | None = None) -> list[dict]:
        _jarvis(context)
        query = _text(query, "query", 500)
        if status is not None and status not in ("inbox", "processed", "archived"):
            raise ValueError("Invalid capture status")
        with connect() as conn:
            rows = conn.execute(
                "SELECT id,source_kind,title,source_url,status,created_at,updated_at FROM capture_items "
                "WHERE owner_id=? AND deleted_at IS NULL AND (? IS NULL OR status=?) "
                "AND (?='' OR instr(lower(title),lower(?))>0 OR instr(lower(content),lower(?))>0) ORDER BY updated_at DESC,id",
                (context.owner_id, status, status, query, query, query),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_capture(self, context: AssistantContext, capture_id: str) -> dict:
        _jarvis(context)
        with connect() as conn:
            return dict(self._capture(conn, context.owner_id, capture_id))

    def set_capture_status(self, context: AssistantContext, capture_id: str, status: str) -> None:
        _jarvis(context)
        if status not in ("inbox", "processed", "archived"):
            raise ValueError("Invalid capture status")
        now = _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._capture(conn, context.owner_id, capture_id)
            conn.execute("UPDATE capture_items SET status=?,updated_at=? WHERE id=? AND owner_id=?", (status, now, capture_id, context.owner_id))
            _audit(conn, context, "capture.status_changed", "capture", capture_id, now)

    def delete_capture(self, context: AssistantContext, capture_id: str) -> None:
        _jarvis(context)
        now = _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._capture(conn, context.owner_id, capture_id)
            conn.execute("UPDATE capture_items SET deleted_at=?,updated_at=? WHERE id=? AND owner_id=?", (now, now, capture_id, context.owner_id))
            _audit(conn, context, "capture.deleted", "capture", capture_id, now)

    def restore_capture(self, context: AssistantContext, capture_id: str) -> None:
        _jarvis(context)
        now = _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM capture_items WHERE id=? AND owner_id=? AND deleted_at IS NOT NULL", (capture_id, context.owner_id)).fetchone()
            if row is None:
                raise NotFound("Deleted capture not found")
            conn.execute("UPDATE capture_items SET deleted_at=NULL,updated_at=? WHERE id=? AND owner_id=?", (now, capture_id, context.owner_id))
            _audit(conn, context, "capture.restored", "capture", capture_id, now)

    def create_note(self, context: AssistantContext, title: str, body: str, source_capture_id: str | None = None,
                    *, confirmed_by_user: bool = False) -> str:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required to save a note")
        title, body = _text(title, "title", 500, required=True).strip(), _text(body, "body", 100000, required=True)
        note_id, now = str(uuid4()), _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if source_capture_id is not None:
                self._capture(conn, context.owner_id, source_capture_id)
            conn.execute("INSERT INTO knowledge_items(id,owner_id,title,body,source_capture_id,created_at,updated_at,deleted_at) VALUES (?,?,?,?,?,?,?,NULL)",
                         (note_id, context.owner_id, title, body, source_capture_id, now, now))
            if source_capture_id is not None:
                conn.execute("UPDATE capture_items SET status='processed',updated_at=? WHERE id=? AND owner_id=?", (now, source_capture_id, context.owner_id))
            _audit(conn, context, "knowledge.created", "knowledge", note_id, now)
        return note_id

    def list_notes(self, context: AssistantContext, query: str = "") -> list[dict]:
        _jarvis(context)
        query = _text(query, "query", 500)
        with connect() as conn:
            rows = conn.execute("SELECT id,title,source_capture_id,created_at,updated_at FROM knowledge_items WHERE owner_id=? AND deleted_at IS NULL AND (?='' OR instr(lower(title),lower(?))>0 OR instr(lower(body),lower(?))>0) ORDER BY updated_at DESC,id",
                                (context.owner_id, query, query, query)).fetchall()
            return [dict(row) for row in rows]

    def get_note(self, context: AssistantContext, note_id: str) -> dict:
        _jarvis(context)
        with connect() as conn:
            note = dict(self._note(conn, context.owner_id, note_id))
            source_id = note["source_capture_id"]
            source = None if source_id is None else conn.execute(
                "SELECT id,title,source_url FROM capture_items WHERE id=? AND owner_id=? AND deleted_at IS NULL",
                (source_id, context.owner_id),
            ).fetchone()
            note["source"] = None if source_id is None else {"id": source_id, "available": source is not None,
                "title": source["title"] if source else None, "source_url": source["source_url"] if source else None}
            return note

    def edit_note(self, context: AssistantContext, note_id: str, title: str, body: str) -> None:
        _jarvis(context)
        title, body = _text(title, "title", 500, required=True).strip(), _text(body, "body", 100000, required=True)
        now = _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._note(conn, context.owner_id, note_id)
            conn.execute("UPDATE knowledge_items SET title=?,body=?,updated_at=? WHERE id=? AND owner_id=?", (title, body, now, note_id, context.owner_id))
            self._invalidate_derived(conn, context.owner_id, note_id)
            _audit(conn, context, "knowledge.edited", "knowledge", note_id, now)

    def delete_note(self, context: AssistantContext, note_id: str) -> None:
        _jarvis(context)
        now = _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._note(conn, context.owner_id, note_id)
            conn.execute("UPDATE knowledge_items SET deleted_at=?,updated_at=? WHERE id=? AND owner_id=?", (now, now, note_id, context.owner_id))
            self._invalidate_derived(conn, context.owner_id, note_id)
            _audit(conn, context, "knowledge.deleted", "knowledge", note_id, now)

    def restore_note(self, context: AssistantContext, note_id: str) -> None:
        _jarvis(context)
        now = _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT id FROM knowledge_items WHERE id=? AND owner_id=? AND deleted_at IS NOT NULL", (note_id, context.owner_id)).fetchone()
            if row is None:
                raise NotFound("Deleted note not found")
            conn.execute("UPDATE knowledge_items SET deleted_at=NULL,updated_at=? WHERE id=? AND owner_id=?", (now, note_id, context.owner_id))
            _audit(conn, context, "knowledge.restored", "knowledge", note_id, now)

    @staticmethod
    def _invalidate_derived(conn, owner_id: str, note_id: str) -> None:
        conn.execute("UPDATE model_requests SET status='invalidated',payload_json='{}' WHERE owner_id=? AND source_type='note' AND source_id=? AND status='pending'", (owner_id, note_id))
        conn.execute("DELETE FROM ai_suggestions WHERE owner_id=? AND source_type='note' AND source_id=?", (owner_id, note_id))

    @staticmethod
    def _capture(conn, owner_id: str, capture_id: str):
        row = conn.execute("SELECT * FROM capture_items WHERE id=? AND owner_id=? AND deleted_at IS NULL", (capture_id, owner_id)).fetchone()
        if row is None:
            raise NotFound("Capture not found")
        return row

    @staticmethod
    def _note(conn, owner_id: str, note_id: str):
        row = conn.execute("SELECT * FROM knowledge_items WHERE id=? AND owner_id=? AND deleted_at IS NULL", (note_id, owner_id)).fetchone()
        if row is None:
            raise NotFound("Knowledge note not found")
        return row
