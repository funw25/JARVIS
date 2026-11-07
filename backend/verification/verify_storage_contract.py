"""Baseline SQLite storage contract with synthetic fault injection."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "verification"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AssistantContext
from app.services.todo_service import TodoService
import app.services.synthetic_knowledge_service as knowledge_module
import storage_view


def fail_audit(*args, **kwargs):
    raise RuntimeError("synthetic audit failure")


with tempfile.TemporaryDirectory(prefix="jarvis-storage-contract-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    with connect() as conn:
        conn.execute("CREATE TABLE contract_probe(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)")
    try:
        with connect() as conn:
            conn.execute("INSERT INTO contract_probe VALUES (1,'rollback')")
            raise RuntimeError("injected failure")
    except RuntimeError:
        pass
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM contract_probe").fetchone()[0] == 0
        conn.execute("INSERT INTO contract_probe VALUES (1,'committed')")
    with connect() as conn:
        row = conn.execute("SELECT id,name FROM contract_probe").fetchone()
        assert row[0] == row["id"] == 1 and row[1] == row["name"] == "committed"
        assert row.keys() == ["id", "name"] and dict(row) == {"id": 1, "name": "committed"}
        duplicate = conn.execute("SELECT 1 AS x, 2 AS x").fetchone()
        assert duplicate[0] == duplicate["x"] == 1 and duplicate[1] == 2
        assert conn.execute("UPDATE contract_probe SET name='updated' WHERE id=1").rowcount == 1
        assert conn.execute("UPDATE contract_probe SET name='missing' WHERE id=2").rowcount == 0
    try:
        with connect() as conn:
            conn.execute("UPDATE contract_probe SET name='must rollback' WHERE id=1")
            conn.execute("INSERT INTO contract_probe VALUES (1,'duplicate id')")
    except storage_view.CONSTRAINT_ERRORS:
        pass
    else:
        raise AssertionError("Constraint violation was not raised")
    with connect() as conn:
        assert conn.execute("SELECT name FROM contract_probe").fetchone()[0] == "updated"
    owner = AssistantContext("contract-owner", "jarvis")
    todo = TodoService()
    original_audit = TodoService._audit
    try:
        TodoService._audit = staticmethod(fail_audit)
        try:
            todo.create(owner, "Fictional task", confirmed_by_user=True, idempotency_key="request-1")
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected synthetic audit failure")
    finally:
        TodoService._audit = staticmethod(original_audit)
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM todos WHERE owner_id=?", (owner.owner_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM todo_create_requests WHERE owner_id=?", (owner.owner_id,)).fetchone()[0] == 0
    task = todo.create(owner, "Fictional task", confirmed_by_user=True, idempotency_key="request-1")
    assert todo.get(owner, task)["title"] == "Fictional task"
    knowledge = knowledge_module.SyntheticKnowledgeService()
    capture = knowledge.create_capture(owner, "Capture", "fictional content", confirmed_by_user=True)
    original_audit = knowledge_module._audit
    try:
        knowledge_module._audit = fail_audit
        try:
            knowledge.create_note(owner, "Note", "fictional body", capture, confirmed_by_user=True)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected synthetic audit failure")
    finally:
        knowledge_module._audit = original_audit
    assert knowledge.get_capture(owner, capture)["status"] == "inbox"
    assert knowledge.list_notes(owner) == []
    print("ROW_SHAPE_TRANSACTION_COMMIT_ROLLBACK_AND_ERRORS=PASS")
    print("TODO_AND_KNOWLEDGE_AUDIT_FAILURE_ATOMICITY=PASS")
print("SQLITE_SYNTHETIC_STORAGE_CONTRACT=PASS")
