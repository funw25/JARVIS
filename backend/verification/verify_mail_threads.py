"""Verify synthetic mail grouping and isolation using an offline fixture."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations
from app.security.assistant_context import AccessDenied, AssistantContext, NotFound
from app.services.synthetic_mail_service import SyntheticMailService
from app.services.synthetic_mail_sync_service import FakeMailboxProvider, FakeMailRecord, SyntheticMailSyncService
from app.services.synthetic_mail_thread_service import SyntheticMailThreadService


def fails(operation, error):
    try:
        operation()
    except error:
        return
    raise AssertionError("Expected rejection")


with tempfile.TemporaryDirectory(prefix="jarvis-thread-check-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner, other = AssistantContext("owner-a", "jarvis"), AssistantContext("owner-b", "jarvis")
    companion = AssistantContext("owner-a", "companion")
    sync, mail, threads = SyntheticMailSyncService(), SyntheticMailService(), SyntheticMailThreadService()
    def record(id, thread, subject, day):
        return FakeMailRecord(id, thread, "sender@example.invalid", subject, "private synthetic body", f"2027-01-0{day}T00:00:00Z")
    first = FakeMailboxProvider("fixture_a", [record("a1", "shared", "Older", 1), record("a2", "shared", "Newer", 2)])
    second = FakeMailboxProvider("fixture_b", [record("b1", "shared", "Other account", 3)])
    foreign = FakeMailboxProvider("fixture_c", [record("c1", "shared", "Foreign", 4)])
    connections = []
    for ctx, dataset, provider in ((owner, "fixture_a", first), (owner, "fixture_b", second), (other, "fixture_c", foreign)):
        connection = sync.create_connection(ctx, dataset, "me@example.invalid", confirmed_by_user=True)
        run = sync.start(ctx, connection, provider, 20, confirmed_by_user=True)
        assert sync.next_page(ctx, run, provider)["status"] == "completed"
        connections.append(connection)
    manual = mail.create_message(owner, "manual@example.invalid", "Manual", "private manual body")
    group = threads.get_provider_thread(owner, connections[0], "shared")
    assert [row["subject"] for row in group["items"]] == ["Newer", "Older"]
    mail.triage(owner, group["items"][0]["id"], "needs_reply", "high")
    result = threads.list_threads(owner)
    assert len(result["items"]) == 3 and not result["truncated"]
    provider_items = [row for row in result["items"] if row["kind"] == "provider"]
    assert {row["connection_id"] for row in provider_items} == set(connections[:2])
    group_info = next(row for row in provider_items if row["connection_id"] == connections[0])
    assert group_info["message_count"] == 2 and group_info["needs_reply_count"] == 1 and group_info["high_priority"] == 1
    assert any(row["latest_mail_id"] == manual and row["kind"] == "manual" for row in result["items"])
    assert "private" not in repr(result) and "private" not in repr(group)
    assert threads.list_threads(owner, limit=1)["truncated"]
    assert threads.get_provider_thread(owner, connections[0], "shared", limit=1)["truncated"]
    fails(lambda: threads.get_provider_thread(other, connections[0], "shared"), NotFound)
    fails(lambda: threads.get_provider_thread(owner, connections[2], "shared"), NotFound)
    fails(lambda: threads.list_threads(companion), AccessDenied)
    fails(lambda: threads.list_threads(owner, limit=True), ValueError)
    sync.revoke(owner, connections[0], confirmed_by_user=True)
    assert len(threads.get_provider_thread(owner, connections[0], "shared")["items"]) == 2
print("SYNTHETIC_THREAD_GROUPING_SCOPE_AND_REVOCATION=PASS")
