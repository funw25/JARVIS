"""Check that simultaneous task changes do not overwrite unrelated fields."""
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.repositories.todo_repository import TodoRepository
from app.security.assistant_context import AssistantContext, NotFound
from app.services.todo_service import TodoService


class PausingRepository(TodoRepository):
    def __init__(self):
        self.read = threading.Event()
        self.release = threading.Event()
        self.second_read = threading.Event()
    def get_owned(self, conn, owner_id, assistant_id, todo_id):
        row = super().get_owned(conn, owner_id, assistant_id, todo_id)
        if threading.current_thread().name.startswith("first-task-"):
            self.read.set()
            if not self.release.wait(3):
                raise RuntimeError("Timed out waiting for test release")
        else:
            self.second_read.set()
        return row


with tempfile.TemporaryDirectory(prefix="jarvis-todo-race-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    ctx = AssistantContext("owner", "jarvis")
    service = TodoService()
    item = service.create(ctx, "Original", priority="normal", confirmed_by_user=True)
    repository = PausingRepository()
    paused = TodoService(repository)
    errors = []
    def first():
        try:
            paused.update(ctx, item, title="Updated")
        except Exception as exc:
            errors.append(exc)
    worker = threading.Thread(target=first, name="first-task-1")
    worker.start()
    assert repository.read.wait(3)
    with ThreadPoolExecutor(max_workers=1) as pool:
        second = pool.submit(paused.update, ctx, item, priority="high")
        try:
            assert not repository.second_read.wait(0.25), "second writer unexpectedly bypassed the transaction lock"
        finally:
            repository.release.set()
        second.result(timeout=5)
    worker.join(timeout=5)
    assert not worker.is_alive() and not errors, errors
    result = service.get(ctx, item)
    assert result["title"] == "Updated" and result["priority"] == "high"
    print("CONCURRENT_PARTIAL_EDITS_PRESERVE_BOTH_CHANGES=PASS")
    service.set_status(ctx, item, "done")
    first_completed = service.get(ctx, item)["completed_at"]
    with connect() as conn:
        count_before = conn.execute("SELECT count(*) FROM audit_events WHERE object_id=? AND event_type='todo.status_changed'", (item,)).fetchone()[0]
    service.set_status(ctx, item, "done")
    with connect() as conn:
        count_after = conn.execute("SELECT count(*) FROM audit_events WHERE object_id=? AND event_type='todo.status_changed'", (item,)).fetchone()[0]
    assert service.get(ctx, item)["completed_at"] == first_completed and count_before == count_after
    service.set_status(ctx, item, "open")
    assert service.get(ctx, item)["completed_at"] is None
    print("REPEATED_COMPLETION_IS_IDEMPOTENT=PASS")

    repository2 = PausingRepository()
    guarded = TodoService(repository2)
    errors.clear()
    def delete_first():
        try:
            guarded.delete(ctx, item)
        except Exception as exc:
            errors.append(exc)
    deleting = threading.Thread(target=delete_first, name="first-task-2")
    deleting.start()
    assert repository2.read.wait(3)
    with ThreadPoolExecutor(max_workers=1) as pool:
        sharing = pool.submit(guarded.share_readonly, ctx, item, "companion", confirmed_by_user=True)
        try:
            assert not repository2.second_read.wait(0.25), "share bypassed the delete transaction"
        finally:
            repository2.release.set()
        try:
            sharing.result(timeout=5)
            raise AssertionError("Deleted todo was shared")
        except NotFound:
            pass
    deleting.join(timeout=5)
    assert not deleting.is_alive() and not errors, errors
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM todo_access_grants WHERE todo_id=? AND revoked_at IS NULL", (item,)).fetchone()[0] == 0
    print("DELETE_AND_SHARE_RACE_FAILS_CLOSED=PASS")
print("TODO_CONCURRENCY_VERIFICATION=PASS")
