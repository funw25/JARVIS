"""Local fictional capture and note tags, not a sync or external integration."""
import unicodedata

from ..database import connect
from ..security.assistant_context import AccessDenied, AssistantContext, NotFound, validate_context
from .synthetic_knowledge_service import _audit, _now


def _jarvis(context: AssistantContext) -> None:
    validate_context(context)
    if context.assistant_id != "jarvis":
        raise AccessDenied("Knowledge tags are available only to Jarvis")


def _tag(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError("Invalid tag")
    label = unicodedata.normalize("NFC", value.strip())
    if not label or len(label) > 64 or any(unicodedata.category(ch).startswith("C") for ch in label):
        raise ValueError("Tag must contain 1 to 64 printable characters")
    key = unicodedata.normalize("NFC", label.casefold())
    if len(key) > 128:
        raise ValueError("Tag is too long after normalization")
    return label, key


def _object(conn, owner: str, kind: str, object_id: str):
    if kind not in ("capture", "note"):
        raise ValueError("Unknown tagged object type")
    if not isinstance(object_id, str) or not object_id or len(object_id) > 128:
        raise ValueError("Invalid object identifier")
    table = "capture_items" if kind == "capture" else "knowledge_items"
    row = conn.execute(f"SELECT id FROM {table} WHERE owner_id=? AND id=? AND deleted_at IS NULL", (owner, object_id)).fetchone()
    if row is None:
        raise NotFound("Synthetic knowledge item not found")
    return row


class SyntheticKnowledgeTagService:
    def add(self, context: AssistantContext, kind: str, object_id: str, tag: str, *, confirmed_by_user: bool = False) -> dict:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        label, key = _tag(tag)
        now = _now()
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _object(conn, context.owner_id, kind, object_id)
            previous = conn.execute("SELECT tag_label FROM synthetic_knowledge_tags WHERE owner_id=? AND object_type=? AND object_id=? AND tag_key=?", (context.owner_id, kind, object_id, key)).fetchone()
            if previous is not None:
                return {"tag": previous["tag_label"], "added": False}
            count = conn.execute("SELECT COUNT(*) FROM synthetic_knowledge_tags WHERE owner_id=? AND object_type=? AND object_id=?", (context.owner_id, kind, object_id)).fetchone()[0]
            if count >= 20:
                raise ValueError("An item may have at most 20 tags")
            conn.execute("INSERT INTO synthetic_knowledge_tags(owner_id,object_type,object_id,tag_key,tag_label,created_at) VALUES (?,?,?,?,?,?)", (context.owner_id, kind, object_id, key, label, now))
            _audit(conn, context, "knowledge_tag.added", kind, object_id, now)
        return {"tag": label, "added": True}

    def remove(self, context: AssistantContext, kind: str, object_id: str, tag: str, *, confirmed_by_user: bool = False) -> dict:
        _jarvis(context)
        if not confirmed_by_user:
            raise AccessDenied("User confirmation is required")
        _, key = _tag(tag)
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _object(conn, context.owner_id, kind, object_id)
            result = conn.execute("DELETE FROM synthetic_knowledge_tags WHERE owner_id=? AND object_type=? AND object_id=? AND tag_key=?", (context.owner_id, kind, object_id, key))
            removed = result.rowcount == 1
            if removed:
                _audit(conn, context, "knowledge_tag.removed", kind, object_id, _now())
        return {"removed": removed}

    def list_for_item(self, context: AssistantContext, kind: str, object_id: str) -> list[str]:
        _jarvis(context)
        with connect() as conn:
            conn.execute("BEGIN")
            _object(conn, context.owner_id, kind, object_id)
            rows = conn.execute("SELECT tag_label FROM synthetic_knowledge_tags WHERE owner_id=? AND object_type=? AND object_id=? ORDER BY tag_key", (context.owner_id, kind, object_id)).fetchall()
            return [row["tag_label"] for row in rows]

    def find(self, context: AssistantContext, tag: str, *, limit: int = 25) -> dict:
        _jarvis(context)
        _, key = _tag(tag)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Tag result limit must be from 1 to 100")
        with connect() as conn:
            rows = conn.execute("""
                SELECT * FROM (
                    SELECT 'capture' AS kind,c.id,c.title,c.updated_at
                    FROM synthetic_knowledge_tags t JOIN capture_items c ON t.object_id=c.id AND t.owner_id=c.owner_id
                    WHERE t.owner_id=? AND t.tag_key=? AND t.object_type='capture' AND c.deleted_at IS NULL AND c.source_kind='manual_synthetic'
                    UNION ALL
                    SELECT 'note' AS kind,n.id,n.title,n.updated_at
                    FROM synthetic_knowledge_tags t JOIN knowledge_items n ON t.object_id=n.id AND t.owner_id=n.owner_id
                    WHERE t.owner_id=? AND t.tag_key=? AND t.object_type='note' AND n.deleted_at IS NULL
                ) ORDER BY updated_at DESC,kind,id LIMIT ?
            """, (context.owner_id, key, context.owner_id, key, limit + 1)).fetchall()
        return {"items": [dict(row) for row in rows[:limit]], "truncated": len(rows) > limit}
