"""J2b synthetic capture/knowledge/todo verification; no accounts or network."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AccessDenied, AssistantContext, NotFound
from app.services.synthetic_knowledge_service import SyntheticKnowledgeService
from app.services.jarvis_knowledge_todo_workflow import JarvisKnowledgeTodoWorkflow
from app.services.todo_service import TodoService


def expect_error(kind, operation, *args, **kwargs):
    try:
        operation(*args, **kwargs)
    except kind:
        return
    raise AssertionError(f"Expected {kind.__name__}")


with tempfile.TemporaryDirectory(prefix="jarvis-j2b-check-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    apply_migrations()
    with connect() as conn:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions[:6] == [1, 2, 3, 4, 5, 6] and versions == list(range(1, len(versions) + 1)), versions
    owner = AssistantContext("owner-a", "jarvis")
    other = AssistantContext("owner-b", "jarvis")
    knowledge = SyntheticKnowledgeService()
    todos = TodoService()
    workflow = JarvisKnowledgeTodoWorkflow(todos)

    expect_error(AccessDenied, knowledge.create_capture, owner, "Plan", "Synthetic text")
    expect_error(ValueError, knowledge.create_capture, owner, "Unsafe", "x", "https://example.com/", confirmed_by_user=True)
    expect_error(ValueError, knowledge.create_capture, owner, "Query", "x", "https://example.invalid/?token=x", confirmed_by_user=True)
    expect_error(ValueError, knowledge.create_capture, owner, "Credentials", "x", "https://user:secret@example.invalid/", confirmed_by_user=True)
    capture = knowledge.create_capture(owner, "项目计划", "合成材料：需要完成设计", "https://example.invalid/plan", confirmed_by_user=True)
    assert knowledge.get_capture(owner, capture)["status"] == "inbox"
    assert knowledge.list_captures(owner, "完成设计")[0]["id"] == capture
    assert knowledge.list_captures(other) == []
    expect_error(NotFound, knowledge.get_capture, other, capture)
    print("CAPTURE_CONFIRMATION_SEARCH_AND_SCOPE=PASS")

    expect_error(AccessDenied, knowledge.create_note, owner, "笔记", "合成笔记", capture)
    expect_error(NotFound, knowledge.create_note, other, "Other", "No access", capture, confirmed_by_user=True)
    assert knowledge.list_notes(other) == []
    note = knowledge.create_note(owner, "设计笔记", "合成笔记：接口约定", capture, confirmed_by_user=True)
    assert knowledge.get_capture(owner, capture)["status"] == "processed"
    assert knowledge.list_notes(owner, "接口约定")[0]["id"] == note
    note_view = knowledge.get_note(owner, note)
    assert note_view["source"]["id"] == capture and note_view["source"]["available"] is True
    knowledge.edit_note(owner, note, "设计笔记更新", "合成笔记：更新接口")
    assert knowledge.get_note(owner, note)["body"] == "合成笔记：更新接口"
    print("KNOWLEDGE_CREATE_SEARCH_EDIT_AND_TRACE=PASS")

    expect_error(AccessDenied, workflow.create_from_source, owner, "note", note, "行动")
    capture_todo = workflow.create_from_source(owner, "capture", capture, "检查来源", confirmed_by_user=True)
    note_todo = workflow.create_from_source(owner, "note", note, "完成接口", confirmed_by_user=True, idempotency_key="note-todo-1")
    assert workflow.create_from_source(owner, "note", note, "完成接口", confirmed_by_user=True, idempotency_key="note-todo-1") == note_todo
    assert workflow.get_sources(owner, capture_todo)[0]["title"] == "项目计划"
    assert workflow.get_sources(owner, note_todo)[0]["title"] == "设计笔记更新"
    expect_error(NotFound, workflow.create_from_source, other, "note", note, "越权", confirmed_by_user=True)
    assert todos.list(other) == []
    assert len(todos.list(owner)) == 2
    expect_error(NotFound, workflow.get_sources, other, note_todo)
    print("SOURCE_TO_TODO_CONFIRMATION_AND_OWNER_SCOPE=PASS")

    knowledge.delete_capture(owner, capture)
    assert knowledge.get_note(owner, note)["source"]["available"] is False
    assert workflow.get_sources(owner, capture_todo)[0]["available"] is False
    expect_error(NotFound, workflow.create_from_source, owner, "capture", capture, "deleted", confirmed_by_user=True)
    assert len(todos.list(owner)) == 2
    knowledge.restore_capture(owner, capture)
    assert knowledge.get_note(owner, note)["source"]["available"] is True
    knowledge.delete_note(owner, note)
    assert workflow.get_sources(owner, note_todo)[0]["available"] is False
    knowledge.restore_note(owner, note)
    assert workflow.get_sources(owner, note_todo)[0]["available"] is True
    print("RECOVERABLE_DELETION_AND_SOURCE_STATE=PASS")

    foreign = knowledge.create_capture(other, "other secret", "other body", confirmed_by_user=True)
    with connect() as conn:
        conn.execute("INSERT INTO todo_sources(todo_id,source_type,source_id,relation) VALUES (?, 'capture', ?, 'derived_from')", (note_todo, foreign))
        metadata = " ".join(row[0] for row in conn.execute("SELECT metadata_json FROM audit_events"))
    foreign_ref = next(item for item in workflow.get_sources(owner, note_todo) if item["source_id"] == foreign)
    assert foreign_ref["available"] is False and foreign_ref["title"] is None
    assert "合成材料" not in metadata and "合成笔记" not in metadata
    print("FORGED_SOURCE_AND_AUDIT_PRIVACY=PASS")

print("J2B_CAPTURE_KNOWLEDGE_VERIFICATION=PASS")
