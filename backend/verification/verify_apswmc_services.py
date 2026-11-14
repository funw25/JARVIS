"""Run synthetic service workflows on the experimental APSW SQLite3MC adapter.

This proves the adapter can carry real domain services (todos, knowledge,
conversations, continuity, shares) while the database is encrypted, using only
temporary fictional data. It does not switch the application's default storage,
and it is not a security review.
"""
import os
import secrets
import sqlite3
from contextlib import closing
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".jarvis-python314-apswmc-probe"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(PROBE))

if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not PROBE.is_dir():
    print("APSWMC_SERVICES=NOT_RUN (isolated Windows CPython 3.14 candidate required)")
    raise SystemExit(2)
try:
    import apsw
except (ImportError, OSError):
    print("APSWMC_SERVICES=NOT_RUN (candidate unavailable)")
    raise SystemExit(2)

from app.security.assistant_context import AccessDenied, AssistantContext, NotFound
from app.services.conversation_service import ContinuityService, ConversationService
from app.services.synthetic_knowledge_service import SyntheticKnowledgeService
from app.services.todo_service import TodoService


def header_is_plaintext(path):
    with open(path, "rb") as source:
        return source.read(16) == b"SQLite format 3\x00"


with tempfile.TemporaryDirectory(prefix="jarvis-apswmc-services-") as directory:
    data_dir = Path(directory) / "data"
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = str(data_dir)
    os.environ["JARVIS_STORAGE_ADAPTER"] = "apswmc"
    os.environ["JARVIS_APSWMC_KEY"] = secrets.token_bytes(32).hex()

    import app.database as database

    assert database.adapter_requested(), "adapter switch was not picked up"
    database.apply_migrations()
    path = database.database_path()
    assert path.is_file(), "adapter did not create the database"
    assert not header_is_plaintext(path), "adapter produced a plaintext database"
    print("SERVICES_ADAPTER_SELECTED_AND_MIGRATED=PASS")

    # The migrated encrypted database must reject a plain sqlite3 reader.
    with closing(sqlite3.connect(path)) as plain, plain:
        try:
            plain.execute("SELECT count(*) FROM sqlite_master").fetchone()
            raise AssertionError("plain sqlite3 read the migrated adapter database")
        except sqlite3.DatabaseError:
            pass
    print("SERVICES_MIGRATED_DATABASE_IS_CIPHERTEXT=PASS")

    owner = AssistantContext("adapter-owner", "jarvis")
    companion = AssistantContext("adapter-owner", "companion")
    todos = TodoService()

    task = todos.create(owner, "Fictional encrypted task", priority="high", confirmed_by_user=True, idempotency_key="adapter-request-1")
    assert todos.get(owner, task)["title"] == "Fictional encrypted task"
    duplicate = todos.create(owner, "Fictional encrypted task", priority="high", confirmed_by_user=True, idempotency_key="adapter-request-1")
    assert duplicate == task, "idempotency key did not return the same todo"
    todos.set_status(owner, task, "done")
    assert todos.get(owner, task)["completed_at"] is not None
    todos.set_status(owner, task, "open")
    assert todos.get(owner, task)["completed_at"] is None
    print("SERVICES_TODO_CREATE_IDEMPOTENT_STATUS=PASS")

    # Ownership isolation and an explicit read-only share both happen on ciphertext.
    assert all(item["id"] != task for item in todos.list(companion))
    try:
        todos.get(companion, task)
        raise AssertionError("companion read a todo it does not own")
    except (NotFound, AccessDenied):
        pass
    grant = todos.share_readonly(owner, task, "companion", confirmed_by_user=True)
    assert any(item["id"] == task for item in todos.list(companion))
    todos.revoke_share(owner, grant)
    assert all(item["id"] != task for item in todos.list(companion))
    print("SERVICES_TODO_ISOLATION_AND_SHARE_ON_CIPHERTEXT=PASS")

    knowledge = SyntheticKnowledgeService()
    capture = knowledge.create_capture(owner, "Fictional capture", "fictional content", confirmed_by_user=True)
    assert knowledge.get_capture(owner, capture)["status"] == "inbox"
    note = knowledge.create_note(owner, "Fictional note", "fictional body", capture, confirmed_by_user=True)
    assert knowledge.get_capture(owner, capture)["status"] != "inbox"
    assert any(item["id"] == note for item in knowledge.list_notes(owner))
    print("SERVICES_KNOWLEDGE_CAPTURE_AND_NOTE=PASS")

    conversations = ConversationService()
    conversation = conversations.create(owner, "Fictional thread")
    conversations.append_message(owner, conversation, "user", "fictional message")
    conversations.append_message(owner, conversation, "assistant", "fictional reply")
    assert len(conversations.history(owner, conversation)) == 2
    print("SERVICES_CONVERSATION_HISTORY=PASS")

    continuity = ContinuityService()
    snapshot = continuity.create_draft(owner, conversation, "fictional summary", ["topic"], ["open item"], [])
    started, _ = continuity.accept_and_start(owner, snapshot, "Fictional continuation")
    assert started and started != conversation
    print("SERVICES_CONTINUITY_SNAPSHOT_AND_NEW_CONVERSATION=PASS")

    # Audit rows must live in the same encrypted database as the domain rows.
    with database.connect() as connection:
        audit = connection.execute(
            "SELECT count(*) FROM audit_events WHERE object_id IN (?,?)", (task, conversation)
        ).fetchone()[0]
    assert audit >= 2, "expected audit events for the todo and conversation"
    print("SERVICES_AUDIT_ROWS_IN_SAME_ENCRYPTED_DATABASE=PASS")

    # Reopening proves persistence: the same encrypted file still returns the row.
    assert todos.get(owner, task)["title"] == "Fictional encrypted task"
    assert not header_is_plaintext(path)
    print("SERVICES_PERSISTENCE_ACROSS_CONNECTIONS=PASS")

print("APSWMC_SERVICE_WORKFLOWS=PASS")
print("APPLICATION_DEFAULT_STORAGE_UNCHANGED=TRUE")
print("BACKUP_RECOVERY_AND_SECURITY_REVIEW=NOT_TESTED")
