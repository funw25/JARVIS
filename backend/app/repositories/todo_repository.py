import sqlite3
from typing import Any

class TodoRepository:
    def create(self, conn: sqlite3.Connection, values: tuple[Any, ...]) -> None:
        conn.execute(
            "INSERT INTO todos (id,owner_id,created_by_assistant,title,description,status,priority,due_at,completed_at,created_at,updated_at,deleted_at) "
            "VALUES (?,?,?,?,?,'open',?,?,NULL,?,?,NULL)", values,
        )

    def get_owned(self, conn: sqlite3.Connection, owner_id: str, assistant_id: str, todo_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM todos WHERE id=? AND owner_id=? AND created_by_assistant=? AND deleted_at IS NULL",
            (todo_id, owner_id, assistant_id),
        ).fetchone()

    def get_visible(self, conn: sqlite3.Connection, owner_id: str, assistant_id: str, todo_id: str) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT t.* FROM todos t WHERE t.id=? AND t.owner_id=? AND t.deleted_at IS NULL AND "
            "(t.created_by_assistant=? OR EXISTS (SELECT 1 FROM todo_access_grants g WHERE g.todo_id=t.id "
            "AND g.owner_id=t.owner_id AND g.to_assistant=? AND g.revoked_at IS NULL))",
            (todo_id, owner_id, assistant_id, assistant_id),
        ).fetchone()

    def list_visible(self, conn: sqlite3.Connection, owner_id: str, assistant_id: str) -> list[sqlite3.Row]:
        return conn.execute(
            "SELECT t.* FROM todos t WHERE t.owner_id=? AND t.deleted_at IS NULL AND "
            "(t.created_by_assistant=? OR EXISTS (SELECT 1 FROM todo_access_grants g WHERE g.todo_id=t.id "
            "AND g.owner_id=t.owner_id AND g.to_assistant=? AND g.revoked_at IS NULL)) "
            "ORDER BY CASE t.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END, (t.due_at IS NULL), t.due_at, t.created_at DESC, t.id",
            (owner_id, assistant_id, assistant_id),
        ).fetchall()

    def add_source(self, conn: sqlite3.Connection, values: tuple[Any, ...]) -> None:
        conn.execute("INSERT OR IGNORE INTO todo_sources (todo_id,source_type,source_id,relation) VALUES (?,?,?,?)", values)
