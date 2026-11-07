"""Exercise Jarvis task search with temporary synthetic data only."""
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations
from app.security.assistant_context import AccessDenied, AssistantContext
from app.services.jarvis_task_query_service import JarvisTaskQueryService
from app.services.todo_service import TodoService


def rejected(function, expected=(ValueError,)):
    try:
        function()
    except expected:
        return
    raise AssertionError("Invalid request accepted")


with tempfile.TemporaryDirectory(prefix="jarvis-task-search-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    owner = AssistantContext("owner-a", "jarvis")
    other = AssistantContext("owner-b", "jarvis")
    companion = AssistantContext("owner-a", "companion")
    todo, search = TodoService(), JarvisTaskQueryService()
    one = todo.create(owner, "Alpha meeting", "synthetic project", "high", "2027-01-03T08:00:00+08:00", confirmed_by_user=True)
    two = todo.create(owner, "Beta meeting", "synthetic note", "normal", "2027-01-04T00:00:00Z", confirmed_by_user=True)
    three = todo.create(owner, "Gamma meeting", "synthetic project", "high", confirmed_by_user=True)
    done = todo.create(owner, "Done meeting", confirmed_by_user=True)
    todo.set_status(owner, done, "done")
    deleted = todo.create(owner, "Deleted meeting", confirmed_by_user=True)
    todo.delete(owner, deleted)
    foreign = todo.create(other, "Foreign meeting", confirmed_by_user=True)
    todo.create(companion, "Companion meeting", confirmed_by_user=True)
    visible = [item["id"] for item in todo.list(owner)]
    assert visible.index(one) < visible.index(two) < visible.index(three)
    result = search.search(owner, query="meeting", limit=1)
    assert [x["id"] for x in result["items"]] == [one] and result["next_cursor"]
    seen = []
    cursor = None
    while True:
        page = search.search(owner, query="meeting", limit=1, cursor=cursor)
        seen.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert [x["id"] for x in seen] == [one, two, three]
    assert all("description" not in item and "owner_id" not in item for item in seen)
    assert foreign not in [item["id"] for item in seen]
    assert search.search(other)["items"][0]["id"] == foreign
    rejected(lambda: search.search(companion), (AccessDenied,))
    print("OWNER_ASSISTANT_SCOPE_AND_PAGINATION=PASS")
    assert [x["id"] for x in search.search(owner, query="project", priority="high")["items"]] == [one, three]
    assert [x["id"] for x in search.search(owner, statuses=("done",))["items"]] == [done]
    assert [x["id"] for x in search.search(owner, due_from="2027-01-02T16:00:00-08:00", due_before="2027-01-04T08:00:00+08:00")["items"]] == [one]
    assert [x["id"] for x in search.search(owner, due_from="2027-01-03T00:00:00.001Z")["items"]] == [two]
    assert [x["id"] for x in search.search(owner, due_before="2027-01-03T00:00:00.001Z")["items"]] == [one]
    assert search.search(owner, due_from="2027-01-03T00:00:00.100Z", due_before="2027-01-03T00:00:00.900Z")["items"] == []
    rejected(lambda: search.search(owner, due_from="2027-01-03T00:00:00.900Z", due_before="2027-01-03T00:00:00.100Z"))
    rejected(lambda: search.search(owner, query="other", limit=1, cursor=result["next_cursor"]))
    rejected(lambda: search.search(other, query="meeting", cursor=result["next_cursor"]))
    raw = result["next_cursor"]
    payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    payload["id"] = "\ud800"
    forged = base64.urlsafe_b64encode(json.dumps(payload).encode("ascii")).decode("ascii").rstrip("=")
    rejected(lambda: search.search(owner, query="meeting", cursor=forged))
    rejected(lambda: search.search(owner, cursor="!"))
    rejected(lambda: search.search(owner, statuses=("open", "open")))
    rejected(lambda: search.search(owner, due_from="2027-01-01"))
    rejected(lambda: search.search(owner, due_from="2027-01-05T00:00:00Z", due_before="2027-01-04T00:00:00Z"))
    rejected(lambda: search.search(owner, limit=True))
    print("FILTERS_CURSORS_AND_INVALID_INPUT=PASS")
print("JARVIS_TASK_SEARCH_VERIFICATION=PASS")
