"""Exercise synthetic knowledge tags, isolation, deletion and concurrent retries."""
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AccessDenied, AssistantContext, NotFound
from app.services.synthetic_knowledge_service import SyntheticKnowledgeService
from app.services.synthetic_knowledge_tag_service import SyntheticKnowledgeTagService


def rejects(call, error):
    try:
        call()
    except error:
        return
    raise AssertionError("Expected " + error.__name__)


with tempfile.TemporaryDirectory(prefix="jarvis-knowledge-tags-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner, other = AssistantContext("owner-a", "jarvis"), AssistantContext("owner-b", "jarvis")
    companion = AssistantContext("owner-a", "companion")
    knowledge, tags = SyntheticKnowledgeService(), SyntheticKnowledgeTagService()
    capture = knowledge.create_capture(owner, "Capture", "fictional secret content", confirmed_by_user=True)
    note = knowledge.create_note(owner, "Note", "fictional note body", confirmed_by_user=True)
    foreign = knowledge.create_note(other, "Other note", "foreign content", confirmed_by_user=True)
    rejects(lambda: tags.add(owner, "capture", capture, "研究"), AccessDenied)
    rejects(lambda: tags.add(companion, "capture", capture, "研究", confirmed_by_user=True), AccessDenied)
    rejects(lambda: tags.add(other, "capture", capture, "研究", confirmed_by_user=True), NotFound)
    rejects(lambda: tags.add(owner, "task", capture, "研究", confirmed_by_user=True), ValueError)
    for invalid in ("", "  ", "x" * 65, "a\nb", "\ud800"):
        rejects(lambda: tags.add(owner, "capture", capture, invalid, confirmed_by_user=True), ValueError)
    def add():
        return tags.add(owner, "capture", capture, "  Research  ", confirmed_by_user=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: add(), range(8)))
    assert sum(result["added"] for result in results) == 1
    assert tags.add(owner, "capture", capture, "RESEARCH", confirmed_by_user=True) == {"tag": "Research", "added": False}
    assert tags.add(owner, "note", note, "research", confirmed_by_user=True)["added"]
    assert tags.add(other, "note", foreign, "research", confirmed_by_user=True)["added"]
    assert tags.add(owner, "note", note, "é", confirmed_by_user=True)["added"]
    assert not tags.add(owner, "note", note, "e\u0301", confirmed_by_user=True)["added"]
    results = tags.find(owner, "RESEARCH", limit=1)
    assert len(results["items"]) == 1 and results["truncated"]
    all_items = tags.find(owner, "research")["items"]
    assert {item["id"] for item in all_items} == {capture, note}
    assert "fictional secret content" not in str(all_items)
    knowledge.delete_capture(owner, capture)
    assert {item["id"] for item in tags.find(owner, "research")["items"]} == {note}
    rejects(lambda: tags.list_for_item(owner, "capture", capture), NotFound)
    knowledge.restore_capture(owner, capture)
    assert tags.list_for_item(owner, "capture", capture) == ["Research"]
    assert tags.remove(owner, "capture", capture, "research", confirmed_by_user=True)["removed"]
    assert not tags.remove(owner, "capture", capture, "research", confirmed_by_user=True)["removed"]
    for i in range(19):
        tags.add(owner, "capture", capture, f"tag-{i}", confirmed_by_user=True)
    tags.add(owner, "capture", capture, "last", confirmed_by_user=True)
    rejects(lambda: tags.add(owner, "capture", capture, "too-many", confirmed_by_user=True), ValueError)
    with connect() as conn:
        audit = " ".join(row[0] for row in conn.execute("SELECT metadata_json FROM audit_events"))
    assert "fictional secret content" not in audit and "Research" not in audit
    print("TAG_NORMALIZATION_IDEMPOTENCY_ISOLATION_AND_LIMITS=PASS")
    print("TAG_SEARCH_SOFT_DELETE_RESTORE_AND_PRIVACY=PASS")
print("SYNTHETIC_KNOWLEDGE_TAG_VERIFICATION=PASS")
