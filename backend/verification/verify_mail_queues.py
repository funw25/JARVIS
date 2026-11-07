"""Verify local synthetic draft and approval queue listing."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AccessDenied, AssistantContext
from app.services.synthetic_mail_service import SyntheticMailService
from app.services.synthetic_mail_queue_service import SyntheticMailQueueService


def rejects(call, error):
    try:
        call()
    except error:
        return
    raise AssertionError("Expected " + error.__name__)


with tempfile.TemporaryDirectory(prefix="jarvis-mail-queues-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner, other = AssistantContext("owner-a", "jarvis"), AssistantContext("owner-b", "jarvis")
    companion = AssistantContext("owner-a", "companion")
    mail, queue = SyntheticMailService(), SyntheticMailQueueService()
    message = mail.create_message(owner, "sender@example.invalid", "Fictional", "secret source body")
    foreign = mail.create_message(other, "sender@example.invalid", "Foreign", "foreign body")
    drafts = [mail.create_draft(owner, message, "me@example.invalid", ["sender@example.invalid"], "Draft " + str(i), "secret draft body") for i in range(4)]
    mail.create_draft(other, foreign, "me@example.invalid", ["sender@example.invalid"], "Foreign draft", "foreign draft body")
    proposals = [mail.prepare_send(owner, draft) for draft in drafts]
    with connect() as conn:
        for draft in drafts:
            conn.execute("UPDATE mail_drafts SET updated_at='2027-01-01T12:00:00.000Z' WHERE id=?", (draft,))
        for proposal in proposals[:3]:
            conn.execute("UPDATE action_proposals SET expires_at='2099-01-01T00:00:00.000Z' WHERE id=?", (proposal["id"],))
        conn.execute("UPDATE action_proposals SET expires_at='2000-01-01T00:00:00.000Z' WHERE id=?", (proposals[3]["id"],))
    cursor, seen = None, []
    while True:
        page = queue.drafts(owner, mail_id=message, limit=1, cursor=cursor)
        seen += [row["id"] for row in page["items"]]
        assert "secret draft body" not in str(page) and "recipients" not in str(page)
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == sorted(drafts, reverse=True)
    cursor, pending = None, []
    while True:
        page = queue.pending_proposals(owner, limit=1, cursor=cursor)
        pending += [row["id"] for row in page["items"]]
        assert not page["execution_available"] and "secret draft body" not in str(page)
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pending == sorted(proposal["id"] for proposal in proposals[:3])
    assert queue.pending_proposals(other)["items"] == []
    rejects(lambda: queue.drafts(companion), AccessDenied)
    rejects(lambda: queue.pending_proposals(companion), AccessDenied)
    first = queue.drafts(owner, mail_id=message, limit=1)
    rejects(lambda: queue.drafts(other, mail_id=message, cursor=first["next_cursor"]), ValueError)
    rejects(lambda: queue.pending_proposals(owner, cursor=first["next_cursor"]), ValueError)
    rejects(lambda: queue.drafts(owner, mail_id="bad id"), ValueError)
    rejects(lambda: queue.drafts(owner, limit=True), ValueError)
    with connect() as conn:
        assert conn.execute("SELECT status FROM action_proposals WHERE id=?", (proposals[3]["id"],)).fetchone()[0] == "pending"
    print("DRAFT_AND_PENDING_QUEUE_PAGINATION_SCOPE_MINIMIZATION=PASS")
    print("EXPIRED_EXCLUDED_WITHOUT_MUTATION=PASS")
print("SYNTHETIC_MAIL_QUEUE_VERIFICATION=PASS")
