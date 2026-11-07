import sqlite3
from typing import Any

class MemoryRepository:
    def get_item(self, conn: sqlite3.Connection, owner_id: str, memory_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT m.*, n.owner_id, n.kind AS namespace_kind FROM memory_items m "
            "JOIN memory_namespaces n ON n.id=m.namespace_id WHERE n.owner_id=? AND m.id=?",
            (owner_id, memory_id),
        ).fetchone()

    def insert_item(self, conn: sqlite3.Connection, values: tuple[Any, ...]) -> None:
        conn.execute(
            "INSERT INTO memory_items (id,namespace_id,created_by_assistant,category,content,confidence,status,approved_at,expires_at,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,'candidate',NULL,?,?,?)", values,
        )

    def add_source(self, conn: sqlite3.Connection, values: tuple[Any, ...]) -> None:
        conn.execute(
            "INSERT OR IGNORE INTO memory_sources (memory_id,source_type,source_id,excerpt,created_at) VALUES (?,?,?,?,?)", values
        )

    def active_grant_exists(self, conn: sqlite3.Connection, owner_id: str, assistant_id: str, namespace_id: str, memory_id: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM memory_access_grants WHERE owner_id=? AND to_assistant=? AND revoked_at IS NULL "
            "AND (memory_id=? OR namespace_id=?) LIMIT 1",
            (owner_id, assistant_id, memory_id, namespace_id),
        ).fetchone()
        return row is not None

    def visible_items(self, conn: sqlite3.Connection, owner_id: str, assistant_id: str) -> list[sqlite3.Row]:
        return conn.execute(
            "SELECT m.id,m.namespace_id,m.created_by_assistant,m.category,m.content,m.confidence,m.status,"
            "m.approved_at,m.expires_at,m.created_at,m.updated_at,n.kind AS namespace_kind "
            "FROM memory_items m JOIN memory_namespaces n ON n.id=m.namespace_id "
            "WHERE n.owner_id=? AND m.status='approved' AND m.deleted_at IS NULL "
            "AND (n.kind=? OR (n.kind='conversation_raw' AND m.created_by_assistant=?) "
            "OR EXISTS (SELECT 1 FROM memory_access_grants g WHERE g.owner_id=n.owner_id "
            "AND g.to_assistant=? AND g.revoked_at IS NULL AND (g.memory_id=m.id OR g.namespace_id=n.id))) "
            "AND (m.expires_at IS NULL OR m.expires_at > strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
            "ORDER BY m.updated_at DESC",
            (owner_id, 'companion' if assistant_id == 'companion' else 'work', assistant_id, assistant_id),
        ).fetchall()

    def add_grant(self, conn: sqlite3.Connection, grant_id: str, owner_id: str, source_assistant: str, target_assistant: str, namespace_id: str | None, memory_id: str | None, now: str) -> None:
        conn.execute(
            "INSERT INTO memory_access_grants (id,owner_id,from_assistant,to_assistant,namespace_id,memory_id,granted_at,revoked_at) VALUES (?,?,?,?,?,?,?,NULL)",
            (grant_id, owner_id, source_assistant, target_assistant, namespace_id, memory_id, now),
        )

    def revoke_grant(self, conn: sqlite3.Connection, owner_id: str, grant_id: str, now: str) -> bool:
        cur = conn.execute(
            "UPDATE memory_access_grants SET revoked_at=? WHERE owner_id=? AND id=? AND revoked_at IS NULL",
            (now, owner_id, grant_id),
        )
        return cur.rowcount == 1
