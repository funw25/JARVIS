"""Verify bounded synthetic knowledge listing and tag filters."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AccessDenied, AssistantContext
from app.services.synthetic_knowledge_service import SyntheticKnowledgeService
from app.services.synthetic_knowledge_tag_service import SyntheticKnowledgeTagService
from app.services.synthetic_knowledge_query_service import SyntheticKnowledgeQueryService


def rejects(call, error):
    try:
        call()
    except error:
        return
    raise AssertionError("Expected " + error.__name__)


with tempfile.TemporaryDirectory(prefix="jarvis-knowledge-query-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner, other = AssistantContext("owner-a", "jarvis"), AssistantContext("owner-b", "jarvis")
    companion = AssistantContext("owner-a", "companion")
    knowledge, tags, query = SyntheticKnowledgeService(), SyntheticKnowledgeTagService(), SyntheticKnowledgeQueryService()
    captures = [knowledge.create_capture(owner, "Capture " + str(i), "fictional body", confirmed_by_user=True) for i in range(3)]
    notes = [knowledge.create_note(owner, "Note " + str(i), "fictional note secret", confirmed_by_user=True) for i in range(3)]
    foreign = knowledge.create_note(other, "Note foreign", "foreign", confirmed_by_user=True)
    for item in notes[:2]:
        tags.add(owner, "note", item, "Research", confirmed_by_user=True)
    tags.add(other, "note", foreign, "Research", confirmed_by_user=True)
    knowledge.set_capture_status(owner, captures[0], "processed")
    with connect() as conn:
        for item in notes:
            conn.execute("UPDATE knowledge_items SET updated_at='2027-01-01T12:00:00.000Z' WHERE id=?", (item,))
    cursor, seen = None, []
    while True:
        page = query.search(owner, "note", query="fictional", limit=1, cursor=cursor)
        assert all("body" not in row for row in page["items"])
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == sorted(notes, reverse=True)
    assert {item["id"] for item in query.search(owner, "note", tag="RESEARCH")["items"]} == set(notes[:2])
    assert query.search(owner, "capture", status="processed")["items"][0]["id"] == captures[0]
    assert len(query.search(other, "note")["items"]) == 1
    rejects(lambda: query.search(companion, "note"), AccessDenied)
    rejects(lambda: query.search(owner, "note", status="inbox"), ValueError)
    rejects(lambda: query.search(owner, "bad"), ValueError)
    rejects(lambda: query.search(owner, "capture", limit=True), ValueError)
    first = query.search(owner, "note", limit=1)
    rejects(lambda: query.search(owner, "capture", limit=1, cursor=first["next_cursor"]), ValueError)
    rejects(lambda: query.search(other, "note", cursor=first["next_cursor"]), ValueError)
    knowledge.delete_note(owner, notes[0])
    assert notes[0] not in {item["id"] for item in query.search(owner, "note", tag="research")["items"]}
    knowledge.restore_note(owner, notes[0])
    assert notes[0] in {item["id"] for item in query.search(owner, "note", tag="research")["items"]}
    print("KNOWLEDGE_PAGINATION_TAGS_AND_SCOPE=PASS")
    print("KNOWLEDGE_DELETE_RESTORE_AND_MINIMIZATION=PASS")
print("SYNTHETIC_KNOWLEDGE_QUERY_VERIFICATION=PASS")
