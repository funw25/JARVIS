"""J5 Jarvis overview verification using temporary synthetic data."""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AssistantContext
from app.services.jarvis_overview_service import JarvisOverviewService
from app.services.synthetic_knowledge_service import SyntheticKnowledgeService
from app.services.synthetic_mail_service import SyntheticMailService
from app.services.synthetic_model_gateway import SyntheticModelGateway
from app.services.todo_service import TodoService


def expect_error(kind, operation, *args, **kwargs):
    try:
        operation(*args, **kwargs)
    except kind:
        return
    raise AssertionError(f"Expected {kind.__name__}")


with tempfile.TemporaryDirectory(prefix="jarvis-j5-check-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner = AssistantContext("owner-a", "jarvis")
    other = AssistantContext("owner-b", "jarvis")
    todos, mail, knowledge = TodoService(), SyntheticMailService(), SyntheticKnowledgeService()
    model, overview = SyntheticModelGateway(), JarvisOverviewService()
    now = datetime(2027, 1, 1, 16, 30, tzinfo=timezone.utc)
    for title, due in (("overdue", "2027-01-01T16:00:00Z"), ("today", "2027-01-01T17:00:00Z"), ("future", "2027-01-02T17:00:00Z"), ("no due", None)):
        todos.create(owner, title, description="private task description", due_at=due, confirmed_by_user=True)
    invalid = todos.create(owner, "invalid due", confirmed_by_user=True)
    done = todos.create(owner, "completed", confirmed_by_user=True)
    todos.set_status(owner, done, "done")
    todos.create(other, "other owner's secret task", confirmed_by_user=True)
    with connect() as conn:
        conn.execute("UPDATE todos SET due_at='malformed' WHERE id=?", (invalid,))

    item = mail.create_message(owner, "sender@example.invalid", "Urgent synthetic mail", "private mail body")
    mail.triage(owner, item, "needs_reply", "high")
    archived = mail.create_message(owner, "sender@example.invalid", "Archived", "hidden body")
    mail.triage(owner, archived, "archived", "normal")
    mail.create_message(other, "other@example.invalid", "other owner secret subject", "foreign body")
    capture = knowledge.create_capture(owner, "Captured item", "private capture content", confirmed_by_user=True)
    knowledge.create_note(owner, "Knowledge note", "private note body", confirmed_by_user=True)
    draft = mail.create_draft(owner, item, "me@example.invalid", ["sender@example.invalid"], "Re: Urgent", "private draft body")
    proposal = mail.prepare_send(owner, draft)
    preview = model.prepare(owner, "mail_summary", item)
    with connect() as conn:
        conn.execute("UPDATE action_proposals SET expires_at='2027-01-01T17:00:00.000Z' WHERE id=?", (proposal["id"],))
        conn.execute("UPDATE model_requests SET expires_at='2027-01-01T17:00:00.000Z' WHERE id=?", (preview["id"],))

    result = overview.snapshot(owner, utc_offset_minutes=480, limit=10, now=now)
    assert result["local_date"] == "2027-01-02"
    assert result["todos"]["open"] == 5
    assert {key: result["todos"][key] for key in ("overdue","due_today","future","without_due","invalid_due")} == {"overdue":1,"due_today":1,"future":1,"without_due":1,"invalid_due":1}
    assert result["todos"]["items"][0]["title"] == "overdue"
    assert result["mail"]["needs_reply"] == 1 and result["mail"]["high_priority"] == 1
    assert result["captures"]["inbox"] == 1 and result["knowledge"]["total"] == 1
    assert result["send_approvals"]["pending"] == 1 and result["model_previews"]["pending"] == 1
    print("COUNTS_TIMEZONE_AND_ACTION_QUEUES=PASS")

    text = json.dumps(result, ensure_ascii=False)
    for secret in ("private task description", "private mail body", "private capture content", "private note body", "private draft body", "other owner secret subject", "other owner's secret task"):
        assert secret not in text, secret
    assert len(overview.snapshot(owner, limit=1, now=now)["todos"]["items"]) == 1
    assert overview.snapshot(other, now=now)["todos"]["open"] == 1
    expect_error(ValueError, overview.snapshot, owner, utc_offset_minutes=841)
    expect_error(ValueError, overview.snapshot, owner, now=datetime(2027, 1, 1))
    extreme = todos.create(owner, "far future", due_at="9999-12-31T23:59:59Z", confirmed_by_user=True)
    assert any(item["id"] == extreme and item["bucket"] == "future" for item in overview.snapshot(owner, utc_offset_minutes=840, limit=50, now=now)["todos"]["items"])
    expect_error(ValueError, todos.create, owner, "out of range", due_at="0001-01-01T00:00:00+14:00", confirmed_by_user=True)
    expect_error(ValueError, overview.snapshot, owner, utc_offset_minutes=840, now=datetime.max.replace(tzinfo=timezone.utc))
    print("OWNER_SCOPE_MINIMAL_FIELDS_AND_VALIDATION=PASS")

print("J5_OVERVIEW_VERIFICATION=PASS")
