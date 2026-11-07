"""Deterministic synthetic randomized checks for local task search."""
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations
from app.security.assistant_context import AssistantContext
from app.services.jarvis_task_query_service import JarvisTaskQueryService
from app.services.todo_service import TodoService

random.seed(235711)
with tempfile.TemporaryDirectory(prefix="jarvis-search-properties-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner, other = AssistantContext("owner-a", "jarvis"), AssistantContext("owner-b", "jarvis")
    tasks, search = TodoService(), JarvisTaskQueryService()
    records = []
    epoch = datetime(2027, 1, 1, tzinfo=timezone.utc)
    for i in range(80):
        who = owner if i % 4 else other
        label = random.choice(("alpha", "beta", "gamma"))
        priority = random.choice(("low", "normal", "high"))
        status = random.choice(("open", "in_progress", "done", "cancelled"))
        due = None if random.randrange(4) == 0 else epoch + timedelta(seconds=random.randrange(0, 100))
        due_text = due.isoformat(timespec="seconds").replace("+00:00", "Z") if due else None
        identifier = tasks.create(who, f"{label} {i}", f"body {label}", priority, due_text, confirmed_by_user=True)
        if status != "open":
            tasks.set_status(who, identifier, status)
        records.append({"owner": who.owner_id, "id": identifier, "title": f"{label} {i}", "description": f"body {label}", "priority": priority, "status": status, "due": due})
    for _ in range(200):
        query = random.choice(("", "alpha", "beta", "missing"))
        statuses = tuple(random.sample(("open", "in_progress", "done", "cancelled"), random.randint(1, 4)))
        priority = random.choice((None, "low", "normal", "high"))
        lower = epoch + timedelta(seconds=random.randrange(0, 90), microseconds=random.choice((0, 100000, 900000)))
        upper = lower + timedelta(seconds=random.randrange(1, 20))
        start = lower.isoformat().replace("+00:00", "Z") if random.choice((True, False)) else None
        end = upper.isoformat().replace("+00:00", "Z") if random.choice((True, False)) else None
        expected = [r for r in records if r["owner"] == owner.owner_id and r["status"] in statuses and
                    (priority is None or r["priority"] == priority) and
                    (not query or query in r["title"] or query in r["description"]) and
                    (start is None or (r["due"] is not None and r["due"] >= lower)) and
                    (end is None or (r["due"] is not None and r["due"] < upper))]
        expected.sort(key=lambda r: (r["due"] is None, r["due"] or epoch, r["id"]))
        cursor, seen = None, []
        while True:
            page = search.search(owner, query=query, statuses=statuses, priority=priority, due_from=start, due_before=end, limit=3, cursor=cursor)
            seen.extend(row["id"] for row in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        assert seen == [r["id"] for r in expected], (query, statuses, priority, start, end)
print("DETERMINISTIC_TASK_SEARCH_PROPERTIES_200_CASES=PASS")
