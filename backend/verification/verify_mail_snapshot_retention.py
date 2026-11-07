"""Verify closed synthetic approval snapshots stop exposing their stored payload."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AssistantContext
from app.services.synthetic_mail_service import SyntheticMailService

with tempfile.TemporaryDirectory(prefix="jarvis-snapshot-check-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner = AssistantContext("owner", "jarvis")
    service = SyntheticMailService()
    mail = service.create_message(owner, "sender@example.invalid", "Synthetic", "body")
    draft = service.create_draft(owner, mail, "me@example.invalid", ["sender@example.invalid"], "Subject", "synthetic draft secret")
    old = service.prepare_send(owner, draft)
    assert service.get_proposal(owner, old["id"])["payload"]["body"] == "synthetic draft secret"
    service.edit_draft(owner, draft, account="me@example.invalid", recipients=["sender@example.invalid"], subject="Subject", body="new synthetic text")
    assert service.get_proposal(owner, old["id"])["payload"] == {}
    rejected = service.prepare_send(owner, draft)
    service.reject(owner, rejected["id"])
    assert service.get_proposal(owner, rejected["id"])["payload"] == {}
    pending = service.prepare_send(owner, draft)
    with connect() as conn:
        conn.execute("UPDATE action_proposals SET expires_at='2000-01-01T00:00:00.000Z' WHERE id=?", (pending["id"],))
    expired = service.get_proposal(owner, pending["id"])
    assert expired["status"] == "expired" and expired["payload"] == {}
    approved = service.prepare_send(owner, draft)
    service.approve(owner, approved["id"], approved["payload_hash"], confirmed_by_user=True)
    with connect() as conn:
        conn.execute("UPDATE action_proposals SET expires_at='2000-01-01T00:00:00.000Z' WHERE id=?", (approved["id"],))
    expired_approval = service.get_proposal(owner, approved["id"])
    assert expired_approval["status"] == "expired" and expired_approval["payload"] == {}
    with connect() as conn:
        rows = conn.execute("SELECT payload_json FROM action_proposals WHERE id IN (?,?,?,?)", (old["id"], rejected["id"], pending["id"], approved["id"])).fetchall()
        assert all(row[0] == "{}" for row in rows)
print("CLOSED_MAIL_SNAPSHOT_LOGICAL_REDACTION=PASS")
print("PHYSICAL_ERASURE_AND_BACKUP_REMOVAL=NOT_TESTED")
