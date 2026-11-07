"""Verify synthetic inbox pagination, scoping, filters, and cursor boundaries."""
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AccessDenied, AssistantContext
from app.services.synthetic_mail_service import SyntheticMailService
from app.services.synthetic_mail_query_service import SyntheticMailQueryService


def rejects(call, error):
    try:
        call()
    except error:
        return
    raise AssertionError("Expected " + error.__name__)


with tempfile.TemporaryDirectory(prefix="jarvis-mail-query-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner, other = AssistantContext("owner-a", "jarvis"), AssistantContext("owner-b", "jarvis")
    companion = AssistantContext("owner-a", "companion")
    mail, inbox = SyntheticMailService(), SyntheticMailQueryService()
    ids = [mail.create_message(owner, "sender@example.invalid", "Meeting " + str(n), "fictional private body") for n in range(5)]
    mail.create_message(other, "sender@example.invalid", "Meeting foreign", "foreign body")
    mail.triage(owner, ids[0], "archived", "normal")
    mail.triage(owner, ids[1], "needs_reply", "high")
    with connect() as conn:
        for item in ids:
            conn.execute("UPDATE mail_items SET received_at='2027-01-01T12:00:00.000Z' WHERE id=?", (item,))
    cursor, seen = None, []
    while True:
        page = inbox.search(owner, query="meeting", limit=1, cursor=cursor)
        assert all("body" not in row for row in page["items"])
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == sorted(ids[1:], reverse=True)
    assert inbox.search(owner, statuses=("archived",))["items"][0]["id"] == ids[0]
    assert inbox.search(owner, priority="high")["items"][0]["id"] == ids[1]
    assert not inbox.search(owner, query="fictional private body")["items"]
    assert len(inbox.search(other)["items"]) == 1
    rejects(lambda: inbox.search(companion), AccessDenied)
    rejects(lambda: inbox.search(owner, statuses=("new", "new")), ValueError)
    rejects(lambda: inbox.search(owner, limit=True), ValueError)
    first = inbox.search(owner, query="meeting", limit=1)
    rejects(lambda: inbox.search(other, query="meeting", cursor=first["next_cursor"]), ValueError)
    rejects(lambda: inbox.search(owner, query="different", cursor=first["next_cursor"]), ValueError)
    raw = first["next_cursor"]
    payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    payload["id"] = "\ud800"
    forged = base64.urlsafe_b64encode(json.dumps(payload).encode("ascii")).decode("ascii").rstrip("=")
    rejects(lambda: inbox.search(owner, query="meeting", cursor=forged), ValueError)
    print("MAIL_FILTERS_PAGINATION_SCOPE_AND_MINIMIZATION=PASS")
    print("MAIL_CURSOR_FILTER_AND_UNICODE_VALIDATION=PASS")
print("SYNTHETIC_MAIL_QUERY_VERIFICATION=PASS")
