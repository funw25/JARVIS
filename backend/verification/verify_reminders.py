"""Verify synthetic reminder rules without a scheduler or real data."""
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AccessDenied, AssistantContext, InvalidTransition, NotFound
from app.services.todo_service import TodoService
from app.services.synthetic_reminder_service import SyntheticReminderService


def rejects(call, error):
    try:
        call()
    except error:
        return
    raise AssertionError("Expected rejection: " + error.__name__)


with tempfile.TemporaryDirectory(prefix="jarvis-reminders-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner = AssistantContext("owner-a", "jarvis")
    other = AssistantContext("owner-b", "jarvis")
    companion = AssistantContext("owner-a", "companion")
    todos, reminders = TodoService(), SyntheticReminderService()
    before = datetime(2027, 1, 1, 10, 59, 59, tzinfo=timezone.utc)
    at = datetime(2027, 1, 1, 11, 0, tzinfo=timezone.utc)
    task = todos.create(owner, "Synthetic title", description="private synthetic detail", due_at="2027-01-01T12:00:00Z", confirmed_by_user=True)
    rejects(lambda: reminders.create_rule(owner, task, 60), AccessDenied)
    rejects(lambda: reminders.create_rule(other, task, 60, confirmed_by_user=True), NotFound)
    rejects(lambda: reminders.create_rule(companion, task, 60, confirmed_by_user=True), AccessDenied)
    for offset in (True, -1, 10081):
        rejects(lambda: reminders.create_rule(owner, task, offset, confirmed_by_user=True), ValueError)
    no_due = todos.create(owner, "No deadline", confirmed_by_user=True)
    rejects(lambda: reminders.create_rule(owner, no_due, 0, confirmed_by_user=True), InvalidTransition)
    rule = reminders.create_rule(owner, task, 60, confirmed_by_user=True)
    assert reminders.create_rule(owner, task, 60, confirmed_by_user=True) == rule
    assert reminders.due(owner, now=before)["items"] == []
    first = reminders.due(owner, now=at)["items"][0]
    assert first["trigger_at"] == "2027-01-01T11:00:00Z"
    assert "description" not in first and "private synthetic detail" not in str(first)
    token = first["token"]
    rejects(lambda: reminders.acknowledge(owner, rule, token, now=at), AccessDenied)
    rejects(lambda: reminders.acknowledge(other, rule, token, now=at, confirmed_by_user=True), NotFound)
    rejects(lambda: reminders.acknowledge(owner, rule, token, now=before, confirmed_by_user=True), InvalidTransition)
    rejects(lambda: reminders.acknowledge(owner, rule, "0" * 64, now=at, confirmed_by_user=True), InvalidTransition)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reminders.acknowledge(owner, rule, token, now=at, confirmed_by_user=True), range(2)))
    assert sorted(r["already_acknowledged"] for r in results) == [False, True]
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM synthetic_reminder_acknowledgements").fetchone()[0] == 1
        assert all("private synthetic detail" not in str(tuple(row)) for row in conn.execute("SELECT * FROM audit_events"))
    assert reminders.due(owner, now=at)["items"] == []
    todos.update(owner, task, title="Changed")
    assert reminders.due(owner, now=at)["items"] == []
    todos.update(owner, task, due_at="2027-01-01T13:00:00Z")
    todos.update(owner, task, due_at="2027-01-01T12:00:00Z")
    current = reminders.due(owner, now=at)["items"][0]
    assert current["token"] != token
    rejects(lambda: reminders.acknowledge(owner, rule, token, now=at, confirmed_by_user=True), InvalidTransition)
    token = current["token"]
    todos.set_status(owner, task, "done")
    assert reminders.due(owner, now=at)["items"] == []
    rejects(lambda: reminders.acknowledge(owner, rule, token, now=at, confirmed_by_user=True), InvalidTransition)
    todos.set_status(owner, task, "open")
    reopened = reminders.due(owner, now=at)["items"][0]["token"]
    assert reopened != token
    todos.delete(owner, task)
    assert reminders.due(owner, now=at)["items"] == []
    todos.restore(owner, task)
    restored = reminders.due(owner, now=at)["items"][0]["token"]
    assert restored != reopened
    reminders.set_enabled(owner, rule, False, confirmed_by_user=True)
    assert reminders.due(owner, now=at)["items"] == []
    rejects(lambda: reminders.create_rule(owner, task, 60, confirmed_by_user=True), InvalidTransition)
    reminders.set_enabled(owner, rule, True, confirmed_by_user=True)
    assert reminders.due(owner, now=at)["items"][0]["token"] != restored
    # The reminder that triggers earlier must come first even if its deadline is later.
    later = todos.create(owner, "Earlier trigger", due_at="2027-01-01T14:00:00Z", confirmed_by_user=True)
    earlier_rule = reminders.create_rule(owner, later, 240, confirmed_by_user=True)
    page = reminders.due(owner, now=at, limit=1)
    assert page["truncated"] and page["items"][0]["rule_id"] == earlier_rule
    rejects(lambda: reminders.due(owner, now=datetime(2027, 1, 1)), ValueError)
    rejects(lambda: reminders.due(owner, now=datetime.min.replace(tzinfo=timezone(timedelta(hours=14)))), ValueError)
    assert reminders.due(other, now=at)["items"] == []
    print("REMINDER_RULES_AND_CONCURRENT_ACK=PASS")
    print("STALE_TOKENS_REOPEN_RESTORE_AND_ORDER=PASS")
print("SYNTHETIC_REMINDER_VERIFICATION=PASS")
