"""Verify deletion serializes with edits and source-based note creation."""
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import apply_migrations, connect
from app.security.assistant_context import AssistantContext, NotFound
from app.services.synthetic_knowledge_service import SyntheticKnowledgeService


class PausedKnowledge(SyntheticKnowledgeService):
    def __init__(self):
        self.read = threading.Event()
        self.release = threading.Event()
        self.second_read = threading.Event()
    def _pause(self):
        if threading.current_thread().name.startswith("first-delete-"):
            self.read.set()
            if not self.release.wait(3):
                raise RuntimeError("Timed out waiting for test release")
        else:
            self.second_read.set()
    def _note(self, conn, owner_id, note_id):
        row = super()._note(conn, owner_id, note_id)
        self._pause()
        return row
    def _capture(self, conn, owner_id, capture_id):
        row = super()._capture(conn, owner_id, capture_id)
        self._pause()
        return row


def race_delete(delete_call, second_call, service):
    errors = []
    def first():
        try:
            delete_call()
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=first, name="first-delete-test")
    thread.start()
    assert service.read.wait(3)
    with ThreadPoolExecutor(max_workers=1) as pool:
        second = pool.submit(second_call)
        try:
            assert not service.second_read.wait(0.25), "second operation bypassed write transaction"
        finally:
            service.release.set()
        try:
            second.result(timeout=5)
            raise AssertionError("operation accepted a deleted source")
        except NotFound:
            pass
    thread.join(timeout=5)
    assert not thread.is_alive() and not errors, errors


with tempfile.TemporaryDirectory(prefix="jarvis-knowledge-race-") as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = directory
    apply_migrations()
    ctx = AssistantContext("owner", "jarvis")
    base = SyntheticKnowledgeService()
    note = base.create_note(ctx, "Original", "fictional body", confirmed_by_user=True)
    paused_note = PausedKnowledge()
    race_delete(lambda: paused_note.delete_note(ctx, note), lambda: paused_note.edit_note(ctx, note, "Edited", "changed"), paused_note)
    with connect() as conn:
        row = conn.execute("SELECT title,body,deleted_at FROM knowledge_items WHERE id=?", (note,)).fetchone()
        assert row["title"] == "Original" and row["body"] == "fictional body" and row["deleted_at"] is not None
    capture = base.create_capture(ctx, "Capture", "fictional source", confirmed_by_user=True)
    paused_capture = PausedKnowledge()
    race_delete(lambda: paused_capture.delete_capture(ctx, capture), lambda: paused_capture.create_note(ctx, "Derived", "body", capture, confirmed_by_user=True), paused_capture)
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM knowledge_items WHERE source_capture_id=?", (capture,)).fetchone()[0] == 0
print("KNOWLEDGE_DELETE_EDIT_AND_SOURCE_RACES=PASS")
