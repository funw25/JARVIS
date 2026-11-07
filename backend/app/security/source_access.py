import sqlite3
from ..security.assistant_context import AssistantContext

_SUPPORTED = {"message", "memory", "continuity_snapshot"}

def source_is_visible(conn: sqlite3.Connection, context: AssistantContext, source_type: str, source_id: str) -> bool:
    if source_type not in _SUPPORTED or not source_id:
        return False
    if source_type == "message":
        row = conn.execute(
            "SELECT 1 FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            "WHERE m.id=? AND c.owner_id=? AND c.assistant_id=? AND c.status!='deleted'",
            (source_id, context.owner_id, context.assistant_id),
        ).fetchone()
        return row is not None
    if source_type == "continuity_snapshot":
        row = conn.execute(
            "SELECT 1 FROM continuity_snapshots WHERE id=? AND owner_id=? AND assistant_id=? AND status='accepted'",
            (source_id, context.owner_id, context.assistant_id),
        ).fetchone()
        return row is not None
    row = conn.execute(
        "SELECT 1 FROM memory_items m JOIN memory_namespaces n ON n.id=m.namespace_id "
        "WHERE m.id=? AND n.owner_id=? AND m.status='approved' AND m.deleted_at IS NULL "
        "AND (m.expires_at IS NULL OR m.expires_at > strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
        "AND (n.kind=? OR (n.kind='conversation_raw' AND m.created_by_assistant=?) "
        "OR EXISTS (SELECT 1 FROM memory_access_grants g WHERE g.owner_id=n.owner_id AND g.to_assistant=? "
        "AND g.revoked_at IS NULL AND (g.memory_id=m.id OR g.namespace_id=n.id)))",
        (source_id, context.owner_id, 'companion' if context.assistant_id == 'companion' else 'work', context.assistant_id, context.assistant_id),
    ).fetchone()
    return row is not None

def validate_source_refs(conn: sqlite3.Connection, context: AssistantContext, refs: list[dict]) -> None:
    for source in refs:
        if not isinstance(source, dict):
            raise ValueError("Source references must be objects")
        source_type, source_id = source.get("type"), source.get("id")
        if source_type not in _SUPPORTED or not isinstance(source_id, str) or not source_is_visible(conn, context, source_type, source_id):
            raise ValueError("Source reference is unavailable in this assistant's scope")


def synthetic_mail_is_visible(conn: sqlite3.Connection, context: AssistantContext, source_id: str) -> bool:
    """Only Jarvis can link the current owner's local synthetic mail."""
    if context.assistant_id != "jarvis" or not source_id:
        return False
    return conn.execute(
        "SELECT 1 FROM mail_items WHERE id=? AND owner_id=? AND source_kind='synthetic'",
        (source_id, context.owner_id),
    ).fetchone() is not None


def synthetic_knowledge_source_is_visible(conn: sqlite3.Connection, context: AssistantContext, source_type: str, source_id: str) -> bool:
    """Check a local synthetic capture or note without widening other namespaces."""
    tables = {"capture": "capture_items", "note": "knowledge_items"}
    table = tables.get(source_type)
    if context.assistant_id != "jarvis" or table is None or not source_id:
        return False
    return conn.execute(f"SELECT 1 FROM {table} WHERE id=? AND owner_id=? AND deleted_at IS NULL",
                        (source_id, context.owner_id)).fetchone() is not None
