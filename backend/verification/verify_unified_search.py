"""Exercise cross-resource keyword search on temporary synthetic data."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations
from app.security.assistant_context import AccessDenied, AssistantContext
from app.services.jarvis_search_service import JarvisSearchService
from app.services.synthetic_knowledge_service import SyntheticKnowledgeService
from app.services.synthetic_mail_service import SyntheticMailService
from app.services.todo_service import TodoService


def fails(call, error=ValueError):
    try:
        call()
    except error:
        return
    raise AssertionError("Invalid search accepted")


with tempfile.TemporaryDirectory(prefix="jarvis-unified-search-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner, other, companion = AssistantContext("owner-a", "jarvis"), AssistantContext("owner-b", "jarvis"), AssistantContext("owner-a", "companion")
    mail, knowledge, todos, search = SyntheticMailService(), SyntheticKnowledgeService(), TodoService(), JarvisSearchService()
    message = mail.create_message(owner, "sender@example.invalid", "Project mail", "private synthetic body")
    task = todos.create(owner, "Task title", "project private description", confirmed_by_user=True)
    capture = knowledge.create_capture(owner, "Project capture", "private synthetic capture", confirmed_by_user=True)
    note = knowledge.create_note(owner, "Note title", "project private note", confirmed_by_user=True)
    deleted = knowledge.create_note(owner, "Project deleted", "hidden", confirmed_by_user=True)
    knowledge.delete_note(owner, deleted)
    foreign = mail.create_message(other, "sender@example.invalid", "Project foreign", "hidden")
    todos.create(companion, "Project companion", confirmed_by_user=True)
    response = search.search(owner, "PROJECT")
    assert {(row["kind"], row["id"]) for row in response["items"]} == {("mail", message), ("todo", task), ("capture", capture), ("note", note)}
    assert all("private" not in repr(row) for row in response["items"])
    assert {row["matched_in"] for row in response["items"]} == {"title", "content"}
    assert search.search(other, "project")["items"][0]["id"] == foreign
    assert {row["kind"] for row in search.search(owner, "project", kinds=("todo", "note"))["items"]} == {"todo", "note"}
    assert search.search(owner, "project", limit=2)["truncated"]
    assert search.search(owner, "%' OR 1=1 --")["items"] == []
    fails(lambda: search.search(companion, "project"), AccessDenied)
    fails(lambda: search.search(owner, ""))
    fails(lambda: search.search(owner, "x" * 201))
    fails(lambda: search.search(owner, "project", kinds=("mail", "mail")))
    fails(lambda: search.search(owner, "project", kinds=("foreign",)))
    fails(lambda: search.search(owner, "project", limit=True))
print("UNIFIED_SEARCH_SCOPE_CONTENT_MINIMIZATION_AND_FILTERS=PASS")
