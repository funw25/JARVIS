"""Local synthetic-data verification for the B1/B2 core; run with Python IDLE (F5)."""
import ast
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

# Parse every backend source file without importing third-party API packages.
for source in BACKEND.rglob("*.py"):
    ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
print("PYTHON_SYNTAX=PASS")

with tempfile.TemporaryDirectory(prefix="jarvis-b1-check-") as temp_dir:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = temp_dir
    from app.database import apply_migrations, connect
    from app.security.assistant_context import AssistantContext, NotFound, InvalidTransition
    from app.services.memory_gateway import MemoryGateway
    from app.services.conversation_service import ConversationService, ContinuityService
    from app.services.todo_service import TodoService

    apply_migrations()
    with connect() as db:
        versions = [r[0] for r in db.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert versions[:5] == [1, 2, 3, 4, 5] and versions == list(range(1, len(versions) + 1)), versions
    print("SQLITE_MIGRATIONS=PASS")

    companion = AssistantContext("verification-owner", "companion")
    jarvis = AssistantContext("verification-owner", "jarvis")
    conversations = ConversationService()
    memories = MemoryGateway()
    continuity = ContinuityService()
    todos = TodoService()

    comp_conversation = conversations.create(companion, "Companion test")
    comp_message = conversations.append_message(companion, comp_conversation, "user", "synthetic memory source")
    memory_id = memories.create_candidate(companion, "preference", "synthetic preference", [{"type":"message","id":comp_message}], 0.9)
    assert memories.list_visible(companion) == []
    memories.approve(companion, memory_id)
    assert any(item["id"] == memory_id for item in memories.list_visible(companion))
    assert all(item["id"] != memory_id for item in memories.list_visible(jarvis))
    grant_id = memories.grant(companion, "jarvis", memory_id=memory_id)
    assert any(item["id"] == memory_id for item in memories.list_visible(jarvis))
    memories.revoke_grant(companion, grant_id)
    assert all(item["id"] != memory_id for item in memories.list_visible(jarvis))
    print("MEMORY_ISOLATION_AND_GRANT=PASS")

    jarvis_conversation = conversations.create(jarvis, "Jarvis test")
    jarvis_message = conversations.append_message(jarvis, jarvis_conversation, "user", "synthetic task source")
    try:
        conversations.history(companion, jarvis_conversation)
        raise AssertionError("cross-assistant conversation read was allowed")
    except NotFound:
        pass
    snapshot_id = continuity.create_draft(companion, comp_conversation, "synthetic continuation", ["topic"], ["open item"], [{"type":"message","id":comp_message}])
    new_conversation, payload = continuity.accept_and_start(companion, snapshot_id, "Resume test")
    assert payload["snapshot_id"] == snapshot_id
    with connect() as db:
        link = db.execute("SELECT continuity_snapshot_id FROM conversations WHERE id=?", (new_conversation,)).fetchone()[0]
    assert link == snapshot_id
    try:
        continuity.accept_and_start(companion, snapshot_id)
        raise AssertionError("accepted snapshot was accepted twice")
    except InvalidTransition:
        pass
    print("CONVERSATION_ISOLATION_AND_CONTINUITY=PASS")

    todo_id = todos.create(jarvis, "Synthetic task", priority="high", due_at="2027-01-02T03:04:05+08:00", sources=[{"type":"message","id":jarvis_message}], confirmed_by_user=True)
    assert any(item["id"] == todo_id for item in todos.list(jarvis))
    assert all(item["id"] != todo_id for item in todos.list(companion))
    todo_share = todos.share_readonly(jarvis, todo_id, "companion", confirmed_by_user=True)
    shared = todos.get(companion, todo_id)
    assert shared["sources"] == []
    try:
        todos.update(companion, todo_id, title="unauthorized edit")
        raise AssertionError("shared todo was editable by recipient")
    except NotFound:
        pass
    todos.revoke_share(jarvis, todo_share)
    assert all(item["id"] != todo_id for item in todos.list(companion))
    todos.set_status(jarvis, todo_id, "done")
    assert todos.get(jarvis, todo_id)["completed_at"] is not None
    todos.set_status(jarvis, todo_id, "open")
    assert todos.get(jarvis, todo_id)["completed_at"] is None
    todos.delete(jarvis, todo_id)
    assert all(item["id"] != todo_id for item in todos.list(jarvis))
    todos.restore(jarvis, todo_id)
    assert any(item["id"] == todo_id for item in todos.list(jarvis))
    print("TODO_SCOPE_LIFECYCLE_AND_SHARE=PASS")

    api_available = False
    try:
        import fastapi, pydantic
        from app.main import app
        assert app.version == "0.2.0"
        # FastAPI >=0.160 / Starlette >=1.0 keep included routers nested inside
        # app.routes (APIRoute objects are no longer flattened), so read the
        # generated OpenAPI schema instead of the internal route objects.
        routes = set(app.openapi()["paths"])
        assert "/api/v1/todos" in routes
        assert "/api/v1/memories" in routes
        api_available = True
        print("FASTAPI_IMPORT_AND_ROUTE_REGISTRATION=PASS")
    except ModuleNotFoundError as exc:
        print("FASTAPI_IMPORT_AND_ROUTE_REGISTRATION=SKIP (dependency missing: %s)" % exc.name)

print("B1_CORE_VERIFICATION=PASS")
print("B2_HTTP_VERIFICATION=NOT_RUN (route import alone is not an HTTP test)")
