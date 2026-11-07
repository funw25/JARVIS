"""Exercise actual project migrations from version 12 with existing synthetic data."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
import app.database as database
from app.security.assistant_context import AssistantContext
from app.services.synthetic_knowledge_service import SyntheticKnowledgeService
from app.services.synthetic_knowledge_tag_service import SyntheticKnowledgeTagService
from app.services.synthetic_reminder_service import SyntheticReminderService
from app.services.todo_service import TodoService

actual_migrations = database.MIGRATIONS
with tempfile.TemporaryDirectory(prefix="jarvis-real-upgrade-") as directory:
    work = Path(directory)
    staged = work / "migrations"
    staged.mkdir()
    files = sorted(actual_migrations.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    assert len(files) >= 14
    for source in files[:12]:
        (staged / source.name).write_bytes(source.read_bytes())
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = str(work / "data")
    database.MIGRATIONS = staged
    database.apply_migrations()
    owner = AssistantContext("upgrade-owner", "jarvis")
    todos, knowledge = TodoService(), SyntheticKnowledgeService()
    task = todos.create(owner, "Existing fictional task", due_at="2027-01-01T12:00:00Z", confirmed_by_user=True)
    note = knowledge.create_note(owner, "Existing fictional note", "fictional body", confirmed_by_user=True)
    with database.connect() as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 12
    for source in files[12:]:
        (staged / source.name).write_bytes(source.read_bytes())
    database.apply_migrations()
    database.apply_migrations()
    assert todos.get(owner, task)["title"] == "Existing fictional task"
    assert todos.get(owner, task)["schedule_revision"] == 1
    assert knowledge.get_note(owner, note)["body"] == "fictional body"
    reminder = SyntheticReminderService().create_rule(owner, task, 60, confirmed_by_user=True)
    assert reminder
    assert SyntheticKnowledgeTagService().add(owner, "note", note, "fictional", confirmed_by_user=True)["added"]
    assert SyntheticKnowledgeTagService().find(owner, "fictional")["items"][0]["id"] == note
    with database.connect() as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == len(files)
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
    print("ACTUAL_V12_TO_CURRENT_DATA_AND_NEW_FEATURES=PASS")
print("REAL_SYNTHETIC_UPGRADE_VERIFICATION=PASS")
