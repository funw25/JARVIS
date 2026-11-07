"""Check local synthetic todo create deduplication and transactional behavior."""
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AssistantContext, InvalidTransition, NotFound
from app.services.synthetic_mail_service import SyntheticMailService
from app.services.todo_service import TodoService


def rejects(call, error):
    try:
        call()
    except error:
        return
    raise AssertionError("Expected request rejection")


with tempfile.TemporaryDirectory(prefix="jarvis-todo-idempotency-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner, other = AssistantContext("owner-a", "jarvis"), AssistantContext("owner-b", "jarvis")
    companion = AssistantContext("owner-a", "companion")
    service = TodoService()
    def create():
        return service.create(owner, "Review", due_at="2027-01-01T08:00:00+08:00", confirmed_by_user=True, idempotency_key="request-1")
    with ThreadPoolExecutor(max_workers=8) as pool:
        identifiers = list(pool.map(lambda _: create(), range(8)))
    assert len(set(identifiers)) == 1
    item = identifiers[0]
    assert service.create(owner, "Review", due_at="2027-01-01T00:00:00Z", confirmed_by_user=True, idempotency_key="request-1") == item
    rejects(lambda: service.create(owner, "Changed", confirmed_by_user=True, idempotency_key="request-1"), InvalidTransition)
    assert service.create(other, "Review", confirmed_by_user=True, idempotency_key="request-1") != item
    assert service.create(companion, "Review", confirmed_by_user=True, idempotency_key="request-1") != item
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM todos WHERE id=?", (item,)).fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM audit_events WHERE object_id=? AND event_type='todo.created'", (item,)).fetchone()[0] == 1
    print("CONCURRENT_RETRY_AND_OWNER_ASSISTANT_SCOPE=PASS")
    service.delete(owner, item)
    assert create() == item
    rejects(lambda: service.get(owner, item), NotFound)
    message = SyntheticMailService().create_message(owner, "sender@example.invalid", "Synthetic", "body")
    missing = str(uuid4())
    rejects(lambda: service.create(owner, "From mail", sources=[{"type":"email", "id":missing}], confirmed_by_user=True, idempotency_key="source-test"), NotFound)
    created = service.create(owner, "From mail", sources=[{"type":"email", "id":message}], confirmed_by_user=True, idempotency_key="source-test")
    assert service.get(owner, created)["sources"][0]["source_id"] == message
    rejects(lambda: service.create(owner, "Bad", confirmed_by_user=True, idempotency_key="a b"), ValueError)
    rejects(lambda: service.create(owner, "Bad", sources=["not-an-object"], confirmed_by_user=True), ValueError)
    print("DELETE_REPLAY_AND_FAILED_SOURCE_ROLLBACK=PASS")
print("TODO_CREATE_IDEMPOTENCY_VERIFICATION=PASS")
