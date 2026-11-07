"""J4b atomic simulated reply-suggestion-to-draft verification."""
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AccessDenied, AssistantContext, InvalidTransition, NotFound
from app.services.jarvis_reply_draft_workflow import JarvisReplyDraftWorkflow
from app.services.synthetic_mail_service import SyntheticMailService
from app.services.synthetic_model_gateway import SyntheticModelGateway


def expect_error(kind, operation, *args, **kwargs):
    try:
        operation(*args, **kwargs)
    except kind:
        return
    raise AssertionError(f"Expected {kind.__name__}")


with tempfile.TemporaryDirectory(prefix="jarvis-j4b-check-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    apply_migrations()
    owner = AssistantContext("owner-a", "jarvis")
    other = AssistantContext("owner-b", "jarvis")
    mail = SyntheticMailService()
    model = SyntheticModelGateway()
    workflow = JarvisReplyDraftWorkflow()
    item = mail.create_message(owner, "sender@example.invalid", "Meeting", "synthetic mail body")
    preview = model.prepare(owner, "mail_reply_suggestion", item)
    suggestion = model.execute(owner, preview["id"], preview["payload_hash"], confirmed_by_user=True)["suggestion_id"]
    fields = {"account": "me@example.invalid", "recipients": ["sender@example.invalid"],
              "subject": "Re: Meeting", "body": "User edited synthetic reply"}
    expect_error(AccessDenied, workflow.accept_as_draft, owner, suggestion, **fields)
    expect_error(NotFound, workflow.accept_as_draft, other, suggestion, **fields, confirmed_by_user=True)
    expect_error(ValueError, workflow.accept_as_draft, owner, suggestion, account="me@example.com", recipients=["sender@example.invalid"], subject="Re", body="x", confirmed_by_user=True)
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM mail_drafts").fetchone()[0] == 0
    print("CONFIRMATION_OWNER_AND_SYNTHETIC_ADDRESS=PASS")

    def accept():
        try:
            return workflow.accept_as_draft(owner, suggestion, **fields, confirmed_by_user=True)
        except InvalidTransition:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: accept(), range(2)))
    accepted = [item for item in outcomes if item is not None]
    assert len(accepted) == 1, outcomes
    draft = mail.get_draft(owner, accepted[0])
    assert draft["body"] == fields["body"] and draft["mail_id"] == item
    assert model.get_suggestion(owner, suggestion)["status"] == "accepted"
    expect_error(InvalidTransition, model.reject_suggestion, owner, suggestion, confirmed_by_user=True)
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM suggestion_draft_links").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM mail_drafts").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM action_proposals").fetchone()[0] == 0
    print("ATOMIC_SINGLE_DRAFT_AND_NO_AUTOMATIC_SEND=PASS")

    proposal = mail.prepare_send(owner, accepted[0])
    assert proposal["payload"]["body"] == fields["body"] and proposal["status"] == "pending"
    expect_error(AccessDenied, mail.approve, owner, proposal["id"], proposal["payload_hash"])
    print("USER_DRAFT_REQUIRES_SEPARATE_SEND_APPROVAL=PASS")

    summary_preview = model.prepare(owner, "mail_summary", item)
    summary_id = model.execute(owner, summary_preview["id"], summary_preview["payload_hash"], confirmed_by_user=True)["suggestion_id"]
    expect_error(InvalidTransition, workflow.accept_as_draft, owner, summary_id, **fields, confirmed_by_user=True)
    missing_item = mail.create_message(owner, "missing@example.invalid", "Later deleted", "synthetic")
    missing_preview = model.prepare(owner, "mail_reply_suggestion", missing_item)
    missing_id = model.execute(owner, missing_preview["id"], missing_preview["payload_hash"], confirmed_by_user=True)["suggestion_id"]
    with connect() as conn:
        conn.execute("DELETE FROM mail_items WHERE id=? AND owner_id=?", (missing_item, owner.owner_id))
    expect_error(NotFound, workflow.accept_as_draft, owner, missing_id, **fields, confirmed_by_user=True)
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM mail_drafts").fetchone()[0] == 1
        audit = " ".join(row[0] for row in conn.execute("SELECT metadata_json FROM audit_events"))
    assert fields["body"] not in audit
    print("WRONG_KIND_MISSING_SOURCE_AND_AUDIT=PASS")

    race_preview = model.prepare(owner, "mail_reply_suggestion", item)
    race_id = model.execute(owner, race_preview["id"], race_preview["payload_hash"], confirmed_by_user=True)["suggestion_id"]
    def race(operation):
        try:
            if operation == "accept":
                workflow.accept_as_draft(owner, race_id, **fields, confirmed_by_user=True)
            else:
                model.reject_suggestion(owner, race_id, confirmed_by_user=True)
            return operation
        except InvalidTransition:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(race, ["accept", "reject"]))
    winners = [outcome for outcome in outcomes if outcome is not None]
    assert len(winners) == 1, outcomes
    assert model.get_suggestion(owner, race_id)["status"] == ("accepted" if winners[0] == "accept" else "rejected")
    with connect() as conn:
        count = conn.execute("SELECT count(*) FROM suggestion_draft_links WHERE suggestion_id=?", (race_id,)).fetchone()[0]
        assert count == (1 if winners[0] == "accept" else 0)
    print("ACCEPT_REJECT_RACE_SINGLE_OUTCOME=PASS")

print("J4B_REPLY_DRAFT_VERIFICATION=PASS")
