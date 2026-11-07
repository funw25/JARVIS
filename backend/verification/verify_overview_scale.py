"""Check overview counts and limits with many fictional records."""
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

with tempfile.TemporaryDirectory(prefix="jarvis-overview-scale-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner = AssistantContext("owner-a", "jarvis")
    created = "2027-01-01T00:00:00.000Z"
    dues = ("2027-01-01T11:00:00Z", "2027-01-01T13:00:00Z", "2027-01-03T00:00:00Z", None, "invalid")
    statuses = ("new", "needs_reply", "archived")
    with connect() as conn:
        conn.executemany(
            "INSERT INTO todos(id,owner_id,created_by_assistant,title,description,status,priority,due_at,created_at,updated_at) VALUES (?,?, 'jarvis',?,'private synthetic description','open','normal',?,?,?)",
            [("task%04d" % i, "owner-a", "Task %d" % i, dues[i % 5], created, created) for i in range(1200)])
        conn.executemany(
            "INSERT INTO mail_items(id,owner_id,source_kind,sender,subject,body,triage_status,priority,received_at,updated_at) VALUES (?,?,'synthetic','sender@example.invalid',?,'private synthetic mail',?,?,?,?)",
            [("mail%04d" % i, "owner-a", "Mail %d" % i, statuses[i % 3], "high" if i % 2 == 0 else "normal", created, created) for i in range(1200)])
        conn.executemany(
            "INSERT INTO capture_items(id,owner_id,source_kind,title,content,status,created_at,updated_at) VALUES (?,?,'manual_synthetic',?,'private synthetic capture',?,?,?)",
            [("capture%04d" % i, "owner-a", "Capture %d" % i, "inbox" if i % 2 == 0 else "processed", created, created) for i in range(1200)])
        conn.executemany(
            "INSERT INTO knowledge_items(id,owner_id,title,body,created_at,updated_at,deleted_at) VALUES (?,?,?,'private synthetic note',?,?,?)",
            [("note%04d" % i, "owner-a", "Note %d" % i, created, created, None if i % 2 == 0 else created) for i in range(1200)])
        conn.execute("INSERT INTO mail_items(id,owner_id,source_kind,sender,subject,body,triage_status,priority,received_at,updated_at) VALUES ('foreign','owner-b','synthetic','foreign@example.invalid','foreign','foreign','new','high',?,?)", (created, created))
    result = JarvisOverviewService().snapshot(owner, utc_offset_minutes=480, limit=3, now=datetime(2027, 1, 1, 12, tzinfo=timezone.utc))
    assert result["todos"]["open"] == 1200
    assert all(result["todos"][key] == 240 for key in ("overdue", "due_today", "future", "without_due", "invalid_due"))
    assert [item["id"] for item in result["todos"]["items"]] == ["task0000", "task0005", "task0010"]
    assert (result["mail"]["new"], result["mail"]["needs_reply"], result["mail"]["high_priority"]) == (400, 400, 400)
    assert result["captures"]["inbox"] == 600 and result["knowledge"]["total"] == 600
    assert all(len(result[key]["items"]) == 3 for key in ("todos", "mail", "captures", "knowledge"))
    assert "private synthetic" not in str(result) and "foreign@example.invalid" not in str(result)
    empty = JarvisOverviewService().snapshot(AssistantContext("owner-empty", "jarvis"))
    assert empty["mail"]["new"] == empty["mail"]["needs_reply"] == empty["mail"]["high_priority"] == 0
    print("LARGE_OVERVIEW_COUNTS_ORDER_LIMIT_AND_SCOPE=PASS")
print("SYNTHETIC_OVERVIEW_SCALE_VERIFICATION=PASS")
