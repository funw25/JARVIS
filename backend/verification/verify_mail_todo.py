"""J2 synthetic Jarvis mail-to-todo verification; no accounts or network."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import apply_migrations, connect
from app.security.assistant_context import AccessDenied, AssistantContext, NotFound
from app.services.jarvis_mail_todo_workflow import JarvisMailTodoWorkflow
from app.services.synthetic_mail_service import SyntheticMailService
from app.services.todo_service import TodoService


def expect_error(kind, operation, *args, **kwargs):
    try:
        operation(*args, **kwargs)
    except kind:
        return
    raise AssertionError(f"Expected {kind.__name__}")


with tempfile.TemporaryDirectory(prefix="jarvis-j2-check-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner = AssistantContext("owner-a", "jarvis")
    other = AssistantContext("owner-b", "jarvis")
    mail = SyntheticMailService()
    todos = TodoService()
    workflow = JarvisMailTodoWorkflow(todos)
    item = mail.create_message(owner, "sender@example.invalid", "Project review", "private synthetic body")
    mail.triage(owner, item, "needs_reply", "high")

    expect_error(AccessDenied, workflow.create_from_mail, owner, item, "Reply to review")
    assert todos.list(owner) == []
    todo_id = workflow.create_from_mail(owner, item, "Reply to review", priority="high", confirmed_by_user=True, idempotency_key="mail-todo-1")
    assert workflow.create_from_mail(owner, item, "Reply to review", priority="high", confirmed_by_user=True, idempotency_key="mail-todo-1") == todo_id
    assert todos.get(owner, todo_id)["sources"] == [{"source_type": "email", "source_id": item, "relation": "derived_from"}]
    refs = workflow.get_mail_sources(owner, todo_id)
    assert len(refs) == 1 and refs[0]["available"] is True
    assert refs[0]["source_id"] == item and refs[0]["subject"] == "Project review"
    assert refs[0]["triage_status"] == "needs_reply" and "body" not in refs[0]
    assert mail.get_message(owner, refs[0]["source_id"])["body"] == "private synthetic body"
    print("CONFIRMED_MAIL_TO_TODO_AND_TRACE=PASS")

    expect_error(NotFound, workflow.create_from_mail, other, item, "Forbidden", confirmed_by_user=True)
    expect_error(NotFound, workflow.create_from_mail, owner, "missing-mail", "Missing", confirmed_by_user=True)
    assert todos.list(other) == []
    assert len(todos.list(owner)) == 1
    expect_error(NotFound, workflow.get_mail_sources, other, todo_id)
    expect_error(NotFound, todos.create, other, "Bypass", sources=[{"type": "email", "id": item}], confirmed_by_user=True)
    assert todos.list(other) == []
    print("OWNER_SCOPE_AND_ATOMIC_ROLLBACK=PASS")

    todos.set_status(owner, todo_id, "done")
    assert workflow.get_mail_sources(owner, todo_id)[0]["source_id"] == item
    todos.delete(owner, todo_id)
    expect_error(NotFound, workflow.get_mail_sources, owner, todo_id)
    todos.restore(owner, todo_id)
    assert workflow.get_mail_sources(owner, todo_id)[0]["available"] is True
    print("TODO_LIFECYCLE_PRESERVES_TRACE=PASS")

    foreign_item = mail.create_message(other, "other@example.invalid", "other owner secret", "other body")
    with connect() as conn:
        conn.execute("INSERT INTO todo_sources(todo_id,source_type,source_id,relation) VALUES (?, 'email', ?, 'derived_from')", (todo_id, foreign_item))
    refs = workflow.get_mail_sources(owner, todo_id)
    hidden = next(ref for ref in refs if ref["source_id"] == foreign_item)
    assert hidden["available"] is False and hidden["subject"] is None and hidden["sender"] is None
    with connect() as conn:
        conn.execute("DELETE FROM mail_items WHERE id=? AND owner_id=?", (item, owner.owner_id))
    refs = workflow.get_mail_sources(owner, todo_id)
    missing = next(ref for ref in refs if ref["source_id"] == item)
    assert missing["available"] is False and missing["subject"] is None
    print("STALE_AND_FORGED_SOURCE_DO_NOT_EXPOSE_MAIL=PASS")

print("J2_MAIL_TO_TODO_VERIFICATION=PASS")
