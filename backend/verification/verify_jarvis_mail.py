"""J1 local synthetic mail verification. No service or account connection is used."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.database import apply_migrations, connect
from app.security.assistant_context import AccessDenied, AssistantContext, InvalidTransition, NotFound
from app.services.synthetic_mail_service import SyntheticMailService


def expect_error(kind, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except kind:
        return
    raise AssertionError(f"Expected {kind.__name__}")


with tempfile.TemporaryDirectory(prefix="jarvis-mail-check-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    apply_migrations()
    with connect() as conn:
        versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
    assert versions[:5] == [1, 2, 3, 4, 5] and versions == list(range(1, len(versions) + 1)), versions
    print("MIGRATIONS_AND_RERUN=PASS")

    owner = AssistantContext("synthetic-owner", "jarvis")
    other_owner = AssistantContext("different-owner", "jarvis")
    mail = SyntheticMailService()
    expect_error(ValueError, mail.create_message, owner, "real@example.com", "No", "No")
    item = mail.create_message(owner, "sender@example.invalid", "Planning session", "Synthetic private body")
    assert mail.get_message(owner, item)["subject"] == "Planning session"
    assert any(row["id"] == item for row in mail.list_messages(owner, "planning"))
    assert mail.list_messages(other_owner) == []
    expect_error(NotFound, mail.get_message, other_owner, item)
    mail.triage(owner, item, "needs_reply", "high")
    assert mail.list_messages(owner, triage_status="needs_reply")[0]["priority"] == "high"
    print("LOCAL_MAIL_TRIAGE_AND_OWNER_ISOLATION=PASS")

    draft = mail.create_draft(owner, item, "me@example.invalid", ["sender@example.invalid"], "Re: Planning session", "Draft version one")
    first = mail.prepare_send(owner, draft)
    assert first["status"] == "pending"
    expect_error(AccessDenied, mail.approve, owner, first["id"], first["payload_hash"])
    expect_error(InvalidTransition, mail.approve, owner, first["id"], "bad", confirmed_by_user=True)
    assert mail.get_proposal(owner, first["id"])["status"] == "pending"
    mail.edit_draft(owner, draft, account="me@example.invalid", recipients=["sender@example.invalid"], subject="Re: Planning session", body="Draft version two")
    assert mail.get_proposal(owner, first["id"])["status"] == "invalidated"
    expect_error(InvalidTransition, mail.approve, owner, first["id"], first["payload_hash"], confirmed_by_user=True)
    second = mail.prepare_send(owner, draft)
    mail.approve(owner, second["id"], second["payload_hash"], confirmed_by_user=True)
    proposal = mail.get_proposal(owner, second["id"])
    assert proposal["status"] == "approved" and proposal["executed_at"] is None
    expect_error(InvalidTransition, mail.approve, owner, second["id"], second["payload_hash"], confirmed_by_user=True)
    assert not hasattr(mail, "send")
    print("DRAFT_APPROVAL_AND_NO_SEND=PASS")

    third = mail.prepare_send(owner, draft)
    assert mail.get_proposal(owner, second["id"])["status"] == "invalidated"
    mail.reject(owner, third["id"])
    assert mail.get_proposal(owner, third["id"])["status"] == "rejected"
    expect_error(InvalidTransition, mail.approve, owner, third["id"], third["payload_hash"], confirmed_by_user=True)
    tampered = mail.prepare_send(owner, draft)
    with connect() as conn:
        conn.execute("UPDATE action_proposals SET payload_json=payload_json || ' ' WHERE id=?", (tampered["id"],))
    expect_error(InvalidTransition, mail.approve, owner, tampered["id"], tampered["payload_hash"], confirmed_by_user=True)
    expired = mail.prepare_send(owner, draft)
    with connect() as conn:
        conn.execute("UPDATE action_proposals SET expires_at='2000-01-01T00:00:00.000Z' WHERE id=?", (expired["id"],))
    expect_error(InvalidTransition, mail.approve, owner, expired["id"], expired["payload_hash"], confirmed_by_user=True)
    assert mail.get_proposal(owner, expired["id"])["status"] == "expired"
    print("PROPOSAL_INVALIDATION_REJECTION_TAMPER_EXPIRY=PASS")

    with connect() as conn:
        metadata = " ".join(row[0] for row in conn.execute("SELECT metadata_json FROM audit_events"))
    assert "Synthetic private body" not in metadata and "Draft version two" not in metadata
    print("AUDIT_METADATA_ONLY=PASS")

print("J1_SYNTHETIC_MAIL_VERIFICATION=PASS")
