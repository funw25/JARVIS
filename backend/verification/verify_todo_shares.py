"""Verify duplicate grants and revocation with fictional tasks."""
import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AssistantContext, NotFound
from app.services.todo_service import TodoService

with tempfile.TemporaryDirectory(prefix="jarvis-share-check-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner, companion = AssistantContext("owner-a", "jarvis"), AssistantContext("owner-a", "companion")
    other = AssistantContext("owner-b", "jarvis")
    service = TodoService()
    item = service.create(owner, "Synthetic private task", confirmed_by_user=True)
    second_item = service.create(owner, "Another task", confirmed_by_user=True)
    grant = service.share_readonly(owner, item, "companion", confirmed_by_user=True)
    assert service.share_readonly(owner, item, "companion", confirmed_by_user=True) == grant
    unaffected = service.share_readonly(owner, second_item, "companion", confirmed_by_user=True)
    with connect() as conn:
        # Simulate an older database containing duplicate active grants.
        conn.execute("INSERT INTO todo_access_grants(id,owner_id,todo_id,from_assistant,to_assistant,granted_at,revoked_at) VALUES (?,?,?,?,?,?,NULL)",
                     (str(uuid4()), owner.owner_id, item, "jarvis", "companion", "2026-01-01T00:00:00Z"))
    assert service.get(companion, item)["id"] == item
    try:
        service.revoke_share(other, grant)
        raise AssertionError("Foreign owner revoked a grant")
    except NotFound:
        pass
    service.revoke_share(owner, grant)
    try:
        service.get(companion, item)
        raise AssertionError("Duplicate grant retained access after revocation")
    except NotFound:
        pass
    assert service.get(companion, second_item)["id"] == second_item
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM todo_access_grants WHERE todo_id=? AND revoked_at IS NULL", (item,)).fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM todo_access_grants WHERE id=? AND revoked_at IS NULL", (unaffected,)).fetchone()[0] == 1
    fresh = service.share_readonly(owner, item, "companion", confirmed_by_user=True)
    assert fresh != grant and service.get(companion, item)["id"] == item
print("TASK_SHARE_IDEMPOTENCE_AND_COMPLETE_REVOCATION=PASS")
