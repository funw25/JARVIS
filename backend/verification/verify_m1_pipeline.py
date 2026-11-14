"""Verify the M1 vertical loop end to end on a temporary database."""
import json
import os
import sqlite3
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
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

        for blank in ("", "  ", "\t\n"):
            def blank_model(text, *, kind):
                return m1_rules.Suggestion("action", "normal", True, False, 0.9,
                                           [{"span": blank, "kind": "model", "reason": "candidate"}])
            try:
                m1_pipeline.suggest(connection, imported["artifact_id"], blank_model)
                raise AssertionError("blank evidence was accepted")
            except ValueError as exc:
                assert "nonblank" in str(exc)
        assert connection.execute("SELECT count(*) FROM m1_suggestions").fetchone()[0] == before
        print("LOOP_BLANK_EVIDENCE_REJECTED=PASS")

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

        again = m1_pipeline.materialize(connection, transcript["decision"]["decision_id"])
        assert again == created
        assert connection.execute("SELECT count(*) FROM todos").fetchone()[0] == totals["todos"]
        assert connection.execute("SELECT count(*) FROM m1_drafts").fetchone()[0] == totals["drafts"]
        assert connection.execute("SELECT count(*) FROM audit_events").fetchone()[0] == totals["audit"]
        print("LOOP_SAME_DECISION_RETURNS_SAME_WORK=PASS")

        failed = m1_pipeline.decide(connection, transcript["suggestion"]["suggestion_id"], "accept")
        original_audit = m1_pipeline.audit
        def fail_after_todo(conn, event_type, *args, **kwargs):
            if event_type == "todo_created":
                raise RuntimeError("simulated failure after todo insertion")
            return original_audit(conn, event_type, *args, **kwargs)
        m1_pipeline.audit = fail_after_todo
        try:
            try:
                m1_pipeline.materialize(connection, failed["decision_id"])
                raise AssertionError("simulated failure did not stop materialization")
            except RuntimeError as exc:
                assert "simulated failure" in str(exc)
        finally:
            m1_pipeline.audit = original_audit
        assert connection.execute("SELECT count(*) FROM todos").fetchone()[0] == totals["todos"]
        assert connection.execute("SELECT count(*) FROM m1_materializations WHERE decision_id=?",
                                  (failed["decision_id"],)).fetchone()[0] == 0
        recovered = m1_pipeline.materialize(connection, failed["decision_id"])
        assert recovered["todo_id"] and recovered["draft_id"]
        assert m1_pipeline.materialize(connection, failed["decision_id"]) == recovered
        print("LOOP_PARTIAL_FAILURE_ROLLBACK_AND_RETRY=PASS")

        interrupted = m1_pipeline.decide(connection, transcript["suggestion"]["suggestion_id"], "accept")
        before_interrupt = connection.execute("SELECT count(*) FROM todos").fetchone()[0]
        def interrupt_after_todo(conn, event_type, *args, **kwargs):
            if event_type == "todo_created":
                raise KeyboardInterrupt("synthetic interruption")
            return original_audit(conn, event_type, *args, **kwargs)
        m1_pipeline.audit = interrupt_after_todo
        try:
            try:
                m1_pipeline.materialize(connection, interrupted["decision_id"])
                raise AssertionError("simulated interruption did not stop materialization")
            except KeyboardInterrupt:
                pass
        finally:
            m1_pipeline.audit = original_audit
        assert connection.execute("SELECT count(*) FROM todos").fetchone()[0] == before_interrupt
        assert connection.execute("SELECT count(*) FROM m1_materializations WHERE decision_id=?",
                                  (interrupted["decision_id"],)).fetchone()[0] == 0
        print("LOOP_INTERRUPTION_ROLLS_BACK=PASS")

        # 6. Nothing in the loop can send: the draft row is marked unsent and no sender exists.
        sent = connection.execute("SELECT count(*) FROM m1_drafts WHERE body LIKE '%已发送%'").fetchone()[0]
        assert sent == 0
        print("LOOP_HAS_NO_SENDER=PASS")

    with database.connect() as connection:
        concurrent_decision = m1_pipeline.decide(connection, transcript["suggestion"]["suggestion_id"], "accept")
    barrier = threading.Barrier(2)
    def concurrent_materialize(_):
        with database.connect() as connection:
            barrier.wait(timeout=10)
            return m1_pipeline.materialize(connection, concurrent_decision["decision_id"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(concurrent_materialize, range(2)))
    assert results[0] == results[1] and results[0]["todo_id"] and results[0]["draft_id"]
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM m1_materializations WHERE decision_id=?",
                                  (concurrent_decision["decision_id"],)).fetchone()[0] == 1
    print("LOOP_CONCURRENT_DECISION_MATERIALIZES_ONCE=PASS")

# Decisions created before migration 0019 have no reliable result mapping.
# Migration must preserve them without inventing an empty or completed result.
legacy = sqlite3.connect(":memory:")
legacy.row_factory = sqlite3.Row
try:
    legacy.executescript((ROOT / "backend" / "migrations" / "0018_m1_artifacts.sql").read_text(encoding="utf-8"))
    legacy.execute("INSERT INTO m1_artifacts VALUES (?,?,?,?,?,?,?,?,?,?)",
                   ("a", "local", "email", "2026-01-01", 1, "hash", None, "fictional", "{}", "2026-01-01"))
    legacy.execute("INSERT INTO m1_suggestions VALUES (?,?,?,?,?,?,?,?,?,?)",
                   ("s", "a", "rules", "fyi", "low", 0, 0, 0.5, "[]", "2026-01-01"))
    legacy.execute("INSERT INTO m1_decisions VALUES (?,?,?,?,?)", ("d", "s", "accept", "{}", "2026-01-01"))
    legacy.executescript((ROOT / "backend" / "migrations" / "0019_m1_materializations.sql").read_text(encoding="utf-8"))
    assert legacy.execute("SELECT state FROM m1_materializations WHERE decision_id='d'").fetchone()[0] == "legacy_unknown"
    try:
        m1_pipeline.materialize(legacy, "d")
        raise AssertionError("legacy decision was replayed without reconciliation")
    except RuntimeError as exc:
        assert "manual reconciliation" in str(exc)
    print("LOOP_LEGACY_DECISIONS_REQUIRE_RECONCILIATION=PASS")
finally:
    legacy.close()

print("M1_PIPELINE=PASS")
