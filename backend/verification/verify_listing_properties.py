"""Compare paginated synthetic listings with independent deterministic oracles."""
import os
import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AssistantContext
from app.services.synthetic_knowledge_query_service import SyntheticKnowledgeQueryService
from app.services.synthetic_mail_query_service import SyntheticMailQueryService

rng = random.Random(6091)
states, priorities = ("new", "needs_reply", "waiting", "archived"), ("low", "normal", "high")
capture_states = ("inbox", "processed", "archived")
with tempfile.TemporaryDirectory(prefix="jarvis-listing-oracles-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner = AssistantContext("owner-a", "jarvis")
    mails, items = [], []
    with connect() as conn:
        for i in range(180):
            timestamp = "2027-01-01T00:00:%02d.000Z" % rng.randrange(10)
            mail = {"id": "mail-%04d" % i, "owner": "owner-a" if i % 3 else "owner-b",
                    "subject": rng.choice(("Alpha meeting", "Beta notes", "Gamma")), "sender": rng.choice(("a@example.invalid", "team@example.invalid")),
                    "body": "private alpha body", "state": rng.choice(states), "priority": rng.choice(priorities), "time": timestamp}
            mails.append(mail)
            conn.execute("INSERT INTO mail_items(id,owner_id,source_kind,sender,subject,body,triage_status,priority,received_at,updated_at) VALUES (?,?,'synthetic',?,?,?,?,?,?,?)",
                         (mail["id"], mail["owner"], mail["sender"], mail["subject"], mail["body"], mail["state"], mail["priority"], timestamp, timestamp))
            kind = rng.choice(("capture", "note"))
            item = {"id": "item-%04d" % i, "owner": "owner-a" if i % 4 else "owner-b", "kind": kind,
                    "title": rng.choice(("Alpha title", "Beta title", "Gamma")), "body": rng.choice(("ordinary text", "magic content")),
                    "status": rng.choice(capture_states), "deleted": i % 11 == 0, "tag": rng.choice((None, "research", "ideas")), "time": timestamp}
            items.append(item)
            if kind == "capture":
                conn.execute("INSERT INTO capture_items(id,owner_id,source_kind,title,content,status,created_at,updated_at,deleted_at) VALUES (?,?,'manual_synthetic',?,?,?,?,?,?)",
                             (item["id"], item["owner"], item["title"], item["body"], item["status"], timestamp, timestamp, timestamp if item["deleted"] else None))
            else:
                conn.execute("INSERT INTO knowledge_items(id,owner_id,title,body,created_at,updated_at,deleted_at) VALUES (?,?,?,?,?,?,?)",
                             (item["id"], item["owner"], item["title"], item["body"], timestamp, timestamp, timestamp if item["deleted"] else None))
            if item["tag"]:
                conn.execute("INSERT INTO synthetic_knowledge_tags(owner_id,object_type,object_id,tag_key,tag_label,created_at) VALUES (?,?,?,?,?,?)",
                             (item["owner"], kind, item["id"], item["tag"], item["tag"], timestamp))
    mail_search, knowledge_search = SyntheticMailQueryService(), SyntheticKnowledgeQueryService()
    for _ in range(120):
        query = rng.choice(("", "ALPHA", "team", "secret"))
        chosen = tuple(rng.sample(states, rng.randrange(1, 5)))
        priority = rng.choice((None, *priorities))
        expected = [row for row in mails if row["owner"] == "owner-a" and row["state"] in chosen and
                    (priority is None or row["priority"] == priority) and
                    (not query or query.lower() in row["subject"].lower() or query.lower() in row["sender"].lower())]
        expected.sort(key=lambda row: (row["time"], row["id"]), reverse=True)
        cursor, seen = None, []
        while True:
            page = mail_search.search(owner, query=query, statuses=chosen, priority=priority, limit=rng.randrange(1, 8), cursor=cursor)
            seen.extend(row["id"] for row in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert seen == [row["id"] for row in expected]
    for _ in range(120):
        kind = rng.choice(("capture", "note"))
        query = rng.choice(("", "ALPHA", "magic", "absent"))
        tag = rng.choice((None, "RESEARCH", "ideas"))
        status = rng.choice((None, *capture_states)) if kind == "capture" else None
        expected = [row for row in items if row["owner"] == "owner-a" and row["kind"] == kind and not row["deleted"] and
                    (status is None or row["status"] == status) and (tag is None or row["tag"] == tag.lower()) and
                    (not query or query.lower() in row["title"].lower() or query.lower() in row["body"].lower())]
        expected.sort(key=lambda row: (row["time"], row["id"]), reverse=True)
        cursor, seen = None, []
        while True:
            page = knowledge_search.search(owner, kind, query=query, tag=tag, status=status, limit=rng.randrange(1, 8), cursor=cursor)
            seen.extend(row["id"] for row in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert seen == [row["id"] for row in expected]
    print("240_RANDOMIZED_MAIL_AND_KNOWLEDGE_QUERY_COMBINATIONS=PASS")
print("SYNTHETIC_LISTING_PROPERTY_VERIFICATION=PASS")
