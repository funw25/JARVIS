"""J8 synthetic read-only mailbox sync verification; no network or OAuth."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AccessDenied, AssistantContext, InvalidTransition, NotFound
from app.services.synthetic_mail_service import SyntheticMailService
from app.services.synthetic_mail_sync_service import FakeMailRecord, FakeMailboxProvider, FakeSyncTransientError, SyntheticMailSyncService
from app.services.synthetic_model_gateway import SyntheticModelGateway


def expect_error(kind, operation, *args, **kwargs):
    try:
        operation(*args, **kwargs)
    except kind:
        return
    raise AssertionError(f"Expected {kind.__name__}")


def record(identifier, body, thread="thread"):
    return FakeMailRecord(identifier, thread, "sender@example.invalid", "Synthetic subject " + identifier, body, "2026-01-01T00:00:00Z")


with tempfile.TemporaryDirectory(prefix="jarvis-j8-check-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    apply_migrations()
    owner, other = AssistantContext("owner-a", "jarvis"), AssistantContext("owner-b", "jarvis")
    sync, mail, model = SyntheticMailSyncService(), SyntheticMailService(), SyntheticModelGateway()
    r1, r2, r3 = record("m1", "private synthetic one"), record("m2", "synthetic two"), record("m3", "synthetic three")
    invalid_date = FakeMailRecord("invalid", "thread", "sender@example.invalid", "subject", "body", "0001-01-01T00:00:00+14:00")
    expect_error(ValueError, invalid_date.normalized)
    fixture = FakeMailboxProvider("dataset", [r1, r2, r2, r3], page_size=2, fail_once_at={2})
    expect_error(AccessDenied, sync.create_connection, owner, "dataset", "me@example.invalid")
    expect_error(ValueError, sync.create_connection, owner, "dataset", "real@example.com", confirmed_by_user=True)
    connection = sync.create_connection(owner, "dataset", "me@example.invalid", confirmed_by_user=True)
    expect_error(NotFound, sync.start, other, connection, fixture, 10, confirmed_by_user=True)
    run = sync.start(owner, connection, fixture, 10, confirmed_by_user=True)
    first = sync.next_page(owner, run, fixture)
    assert first["status"] == "running" and first["processed_count"] == 2 and first["imported_count"] == 2 and first["next_cursor"] == "2"
    replacement = FakeMailboxProvider("dataset", [r3, r2, r2, r1], page_size=2)
    expect_error(InvalidTransition, sync.next_page, owner, run, replacement)
    assert sync.get_run(owner, run)["next_cursor"] == "2" and len(mail.list_messages(owner)) == 2
    expect_error(FakeSyncTransientError, sync.next_page, owner, run, fixture)
    assert sync.get_run(owner, run)["next_cursor"] == "2" and len(mail.list_messages(owner)) == 2
    completed = sync.next_page(owner, run, fixture)
    assert completed["status"] == "completed" and completed["processed_count"] == 4
    assert completed["imported_count"] == 3 and completed["unchanged_count"] == 1
    assert len(mail.list_messages(owner)) == 3 and mail.list_messages(other) == []
    expect_error(InvalidTransition, sync.next_page, owner, run, fixture)
    print("PAGINATION_SNAPSHOT_BINDING_DUPLICATES_AND_RETRY=PASS")

    isolated = AssistantContext("owner-c", "jarvis")
    unstable = FakeMailboxProvider("unstable", [r1, r2], page_size=1)
    unstable_connection = sync.create_connection(isolated, "unstable", "me@example.invalid", confirmed_by_user=True)
    unstable_run = sync.start(isolated, unstable_connection, unstable, 10, confirmed_by_user=True)
    original_page = unstable.page
    def changing_page(cursor, limit):
        result = original_page(cursor, limit)
        unstable.records = tuple(reversed(unstable.records))
        return result
    unstable.page = changing_page
    expect_error(InvalidTransition, sync.next_page, isolated, unstable_run, unstable)
    assert sync.get_run(isolated, unstable_run)["next_cursor"] == "0" and mail.list_messages(isolated) == []
    print("MID_PAGE_FIXTURE_CHANGE_ROLLS_BACK=PASS")
    legacy_fixture = FakeMailboxProvider("legacy", [r1], page_size=1)
    legacy_connection = sync.create_connection(isolated, "legacy", "me@example.invalid", confirmed_by_user=True)
    legacy_run = sync.start(isolated, legacy_connection, legacy_fixture, 10, confirmed_by_user=True)
    with connect() as conn:
        conn.execute("UPDATE synthetic_sync_runs SET dataset_fingerprint=NULL WHERE id=?", (legacy_run,))
    expect_error(InvalidTransition, sync.next_page, isolated, legacy_run, legacy_fixture)
    assert sync.get_run(isolated, legacy_run)["next_cursor"] == "0" and mail.list_messages(isolated) == []
    print("LEGACY_RUN_REQUIRES_RESTART=PASS")

    with connect() as conn:
        mail_id = conn.execute("SELECT mail_id FROM synthetic_mail_links WHERE connection_id=? AND external_id='m1'", (connection,)).fetchone()[0]
    mail.triage(owner, mail_id, "needs_reply", "high")
    draft = mail.create_draft(owner, mail_id, "me@example.invalid", ["sender@example.invalid"], "Re", "synthetic draft")
    proposal = mail.prepare_send(owner, draft)
    prior = model.prepare(owner, "mail_summary", mail_id)
    suggestion = model.execute(owner, prior["id"], prior["payload_hash"], confirmed_by_user=True)["suggestion_id"]
    pending = model.prepare(owner, "mail_summary", mail_id)
    changed = FakeMailboxProvider("dataset", [record("m1", "updated synthetic one"), r2, r3], page_size=2)
    update_run = sync.start(owner, connection, changed, 10, confirmed_by_user=True)
    update_first = sync.next_page(owner, update_run, changed)
    assert update_first["updated_count"] == 1 and update_first["unchanged_count"] == 1
    assert mail.get_message(owner, mail_id)["triage_status"] == "needs_reply"
    assert mail.get_message(owner, mail_id)["priority"] == "high"
    assert mail.get_proposal(owner, proposal["id"])["status"] == "invalidated"
    assert model.get_request(owner, pending["id"])["status"] == "invalidated"
    expect_error(NotFound, model.get_suggestion, owner, suggestion)
    assert sync.next_page(owner, update_run, changed)["status"] == "completed"
    assert len(mail.list_messages(owner)) == 3
    print("IDEMPOTENT_UPDATE_PRESERVES_TRIAGE_INVALIDATES_DERIVED=PASS")

    conflict = FakeMailboxProvider("dataset", [record("x", "one"), record("x", "different")], page_size=2)
    conflict_run = sync.start(owner, connection, conflict, 10, confirmed_by_user=True)
    expect_error(ValueError, sync.next_page, owner, conflict_run, conflict)
    assert sync.get_run(owner, conflict_run)["next_cursor"] == "0" and len(mail.list_messages(owner)) == 3
    limited = sync.start(owner, connection, changed, 1, confirmed_by_user=True)
    limited_state = sync.next_page(owner, limited, changed)
    assert limited_state["status"] == "completed" and limited_state["processed_count"] == 1 and limited_state["next_cursor"] == "1"
    print("CONFLICT_ROLLBACK_AND_USER_LIMIT=PASS")

    active = sync.start(owner, connection, changed, 10, confirmed_by_user=True)
    expect_error(AccessDenied, sync.revoke, owner, connection)
    sync.revoke(owner, connection, confirmed_by_user=True)
    assert sync.get_run(owner, active)["status"] == "cancelled"
    expect_error(InvalidTransition, sync.next_page, owner, active, changed)
    expect_error(InvalidTransition, sync.start, owner, connection, changed, 10, confirmed_by_user=True)
    assert len(mail.list_messages(owner)) == 3
    with connect() as conn:
        audit = " ".join(row[0] for row in conn.execute("SELECT metadata_json FROM audit_events"))
    assert "private synthetic one" not in audit and "updated synthetic one" not in audit
    print("REVOCATION_AND_AUDIT_PRIVACY=PASS")

print("J8_SYNTHETIC_SYNC_VERIFICATION=PASS")
