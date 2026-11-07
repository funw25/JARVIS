import sqlite3
from typing import Any

class ConversationRepository:
    def create(self, conn: sqlite3.Connection, values: tuple[Any, ...]) -> None:
        conn.execute(
            "INSERT INTO conversations (id,owner_id,assistant_id,title,status,created_at,updated_at,continuity_snapshot_id) "
            "VALUES (?,?,?,?,'active',?,?,?)", values,
        )

    def get(self, conn: sqlite3.Connection, owner_id: str, assistant_id: str, conversation_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM conversations WHERE id=? AND owner_id=? AND assistant_id=? AND status!='deleted'",
            (conversation_id, owner_id, assistant_id),
        ).fetchone()

    def list_for_owner(self, conn: sqlite3.Connection, owner_id: str, assistant_id: str) -> list[sqlite3.Row]:
        return conn.execute(
            "SELECT * FROM conversations WHERE owner_id=? AND assistant_id=? AND status!='deleted' ORDER BY updated_at DESC",
            (owner_id, assistant_id),
        ).fetchall()

    def add_message(self, conn: sqlite3.Connection, message_id: str, conversation_id: str, role: str, body: str, refs_json: str, created_at: str) -> None:
        conn.execute(
            "INSERT INTO messages (id,conversation_id,role,body,source_refs_json,created_at) VALUES (?,?,?,?,?,?)",
            (message_id, conversation_id, role, body, refs_json, created_at),
        )
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (created_at, conversation_id))

    def messages(self, conn: sqlite3.Connection, conversation_id: str) -> list[sqlite3.Row]:
        return conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at,id", (conversation_id,)).fetchall()

class ContinuityRepository:
    def create(self, conn: sqlite3.Connection, values: tuple[Any, ...]) -> None:
        conn.execute(
            "INSERT INTO continuity_snapshots (id,owner_id,assistant_id,conversation_id,summary,topics_json,open_items_json,source_refs_json,status,created_at,accepted_at) "
            "VALUES (?,?,?,?,?,?,?,?, 'draft', ?, NULL)", values,
        )

    def get(self, conn: sqlite3.Connection, owner_id: str, assistant_id: str, snapshot_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM continuity_snapshots WHERE id=? AND owner_id=? AND assistant_id=?",
            (snapshot_id, owner_id, assistant_id),
        ).fetchone()

    def accept(self, conn: sqlite3.Connection, snapshot_id: str, accepted_at: str) -> None:
        conn.execute("UPDATE continuity_snapshots SET status='accepted',accepted_at=? WHERE id=?", (accepted_at, snapshot_id))
