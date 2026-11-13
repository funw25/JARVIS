"""Verify the M1 vertical loop end to end on a temporary database."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["JARVIS_DATA_MODE"] = "synthetic"
for name in ("JARVIS_STORAGE_ADAPTER", "JARVIS_APSWMC_KEY", "JARVIS_VAULT_DIR"):
    os.environ.pop(name, None)

import app.database as database
from usecases import m1_pipeline
from usecases import m1_rules

DATASET = ROOT / "backend" / "evals" / "m1" / "artifacts.jsonl"
rows = {row["id"]: row for row in m1_pipeline.load_dataset(DATASET)}


def audit_events(connection, object_ids):
    placeholders = ",".join("?" for _ in object_ids)
    return [
        row["event_type"] for row in connection.execute(
            "SELECT event_type FROM audit_events WHERE object_id IN (%s) ORDER BY created_at" % placeholders,
            tuple(object_ids),
        )
    ]


with tempfile.TemporaryDirectory(prefix="jarvis-m1-pipeline-", ignore_cleanup_errors=True) as directory:
    os.environ["JARVIS_DATA_DIR"] = directory
    database.apply_migrations()
    with database.connect() as connection:
        # 1. Happy path: import -> suggestion -> accept -> todo + draft -> audit.
        transcript = m1_pipeline.run_item(connection, rows["m1-005"], action="accept")
        created = transcript["created"]
        assert created["todo_id"] and created["draft_id"], created
        assert transcript["suggestion"]["evidence_supported"] is True
        assert transcript["suggestion"]["classification"] == "needs_reply"
        events = audit_events(connection, [transcript["artifact"]["artifact_id"],
                                           transcript["suggestion"]["suggestion_id"],
                                           transcript["decision"]["decision_id"],
                                           created["todo_id"], created["draft_id"]])
        for expected in ("artifact_imported", "suggestion_created", "decision_recorded", "todo_created", "draft_created"):
            assert expected in events, (expected, events)
        todo = connection.execute("SELECT title, priority, status FROM todos WHERE id=?", (created["todo_id"],)).fetchone()
        assert todo["priority"] == "high" and todo["status"] == "open", dict(todo)
        draft = connection.execute("SELECT subject, body FROM m1_drafts WHERE id=?", (created["draft_id"],)).fetchone()
        assert "未发送" in draft["body"], draft["body"]
        print("LOOP_IMPORT_SUGGEST_DECIDE_CREATE_AUDIT=PASS")

        # 2. Duplicate text is detected and audited, not silently re-created as new.
        original = m1_pipeline.run_item(connection, rows["m1-003"], action="accept")
        duplicate = m1_pipeline.run_item(connection, rows["m1-049"], action="accept")
        assert duplicate["suggestion"]["duplicate_of"] == original["artifact"]["artifact_id"], duplicate["suggestion"]
        duplicate_of = duplicate["suggestion"]["duplicate_of"]
        first = connection.execute("SELECT id FROM m1_artifacts WHERE id=?", (duplicate_of,)).fetchone()
        assert first is not None, duplicate_of
        print("LOOP_DUPLICATE_DETECTED=PASS")

        # 3. User modification: turning the todo off must still create the draft.
        modified = m1_pipeline.run_item(connection, rows["m1-006"], action="modify", edits={"todo": False, "title": "用户改写的标题"})
        assert modified["created"]["todo_id"] is None, modified["created"]
        assert modified["created"]["draft_id"], modified["created"]
        assert modified["created"]["title"] == "用户改写的标题"
        print("LOOP_USER_MODIFICATION_RESPECTED=PASS")

        # 4. Rejection creates nothing but is still audited.
        rejected = m1_pipeline.run_item(connection, rows["m1-041"], action="reject")
        assert rejected["created"]["todo_id"] is None and rejected["created"]["draft_id"] is None
        assert rejected["created"]["skipped"] is True
        events = audit_events(connection, [rejected["decision"]["decision_id"]])
        assert "decision_recorded" in events, events
        assert rejected["suggestion"]["classification"] == "injection"
        assert rejected["suggestion"]["priority"] == "high"
        print("LOOP_INJECTION_FLAGGED_AND_REJECTED=PASS")

        # 5. Injection items never produce work even when accepted.
        accepted = m1_pipeline.run_item(connection, rows["m1-042"], action="accept")
        assert accepted["suggestion"]["classification"] == "injection"
        assert accepted["created"]["todo_id"] is None and accepted["created"]["draft_id"] is None
        print("LOOP_ACCEPTED_INJECTION_CREATES_NO_ACTION=PASS")

        # 5a. A model cannot turn a rule-detected injection into work.
        imported = m1_pipeline.import_artifact(connection, rows["m1-041"])
        def unsafe_model(text, *, kind):
            return m1_rules.Suggestion("action", "high", True, True, 0.9,
                                       [{"span": "管理员密码", "kind": "model", "reason": "candidate"}])
        unsafe_model.name = "model:synthetic-unsafe"
        screened = m1_pipeline.suggest(connection, imported["artifact_id"], unsafe_model)
        assert screened["engine"] == unsafe_model.name + "+rules-veto"
        assert screened["classification"] == "injection" and not screened["todo"] and not screened["draft"]
        assert screened["evidence_supported"] is True
        assert screened["rule_veto"] is True
        print("LOOP_RULE_VETO_BLOCKS_MODEL_INJECTION_ACTION=PASS")

        # 5b. Unsupported source claims are refused before a suggestion is stored.
        imported = m1_pipeline.import_artifact(connection, rows["m1-003"])
        before = connection.execute("SELECT count(*) FROM m1_suggestions").fetchone()[0]
        def unsupported_model(text, *, kind):
            return m1_rules.Suggestion("needs_reply", "normal", False, True, 0.9,
                                       [{"span": "invented source", "kind": "model", "reason": "candidate"}])
        try:
            m1_pipeline.suggest(connection, imported["artifact_id"], unsupported_model)
            raise AssertionError("unsupported evidence was accepted")
        except ValueError as exc:
            assert "verbatim" in str(exc)
        assert connection.execute("SELECT count(*) FROM m1_suggestions").fetchone()[0] == before
        print("LOOP_UNSUPPORTED_EVIDENCE_REJECTED=PASS")

        # 5c. Editing an injection decision cannot re-enable actions.
        edited = m1_pipeline.decide(connection, accepted["suggestion"]["suggestion_id"], "modify", {"todo": True})
        try:
            m1_pipeline.materialize(connection, edited["decision_id"])
            raise AssertionError("edited injection created work")
        except ValueError as exc:
            assert "injection-classified" in str(exc)
        print("LOOP_INJECTION_EDIT_CANNOT_CREATE_ACTION=PASS")

        totals = {
            "todos": connection.execute("SELECT count(*) FROM todos").fetchone()[0],
            "drafts": connection.execute("SELECT count(*) FROM m1_drafts").fetchone()[0],
            "audit": connection.execute("SELECT count(*) FROM audit_events").fetchone()[0],
            "artifacts": connection.execute("SELECT count(*) FROM m1_artifacts").fetchone()[0],
        }
        assert totals["todos"] == 1 and totals["drafts"] == 4, totals
        assert totals["audit"] >= 15, totals
        print("LOOP_ROW_TOTALS=PASS " + json.dumps(totals))

        # 6. Nothing in the loop can send: the draft row is marked unsent and no sender exists.
        sent = connection.execute("SELECT count(*) FROM m1_drafts WHERE body LIKE '%已发送%'").fetchone()[0]
        assert sent == 0
        print("LOOP_HAS_NO_SENDER=PASS")

print("M1_PIPELINE=PASS")
