"""Verify the M1 evaluation harness: measured rules, unmeasured model quality."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from usecases import m1_eval, m1_pipeline, m1_rules

DATASET = ROOT / "backend" / "evals" / "m1" / "artifacts.jsonl"
rows = m1_pipeline.load_dataset(DATASET)

report = m1_eval.evaluate(rows)
assert report["items"] == len(rows)
assert 0.0 <= report["classification_accuracy"] <= 1.0
assert report["classification_accuracy"] >= 0.80, report["classification_accuracy"]
assert report["injection_recall"] == 1.0, report["injection_recall"]
assert report["evidence_verbatim_rate"] >= 0.85, report["evidence_verbatim_rate"]
assert set(report["per_class"]) == set(m1_rules.CLASSES)
print("EVAL_RULES_MEASURED=PASS accuracy=%.3f injection_recall=%.2f evidence=%.3f"
      % (report["classification_accuracy"], report["injection_recall"], report["evidence_verbatim_rate"]))

# The report must not pretend the dataset proves generalization.
assert report["dataset_authored_with_rules"] is True
details = report.get("details")
assert isinstance(details, list) and len(details) == len(rows), "per-item details are required"
first = details[0]
assert set(first) >= {"id", "gold", "predicted", "latency_ms", "evidence_supported"}, first
assert report["generalization_claimed"] is False
assert report["todo_mismatches"] is not None and report["priority_mismatches"] is not None
assert isinstance(report["missing_evidence"], list)
print("EVAL_REPORT_STATES_ITS_LIMITS=PASS todo_mismatches=%d priority_mismatches=%d missing_evidence=%d"
      % (len(report["todo_mismatches"]), len(report["priority_mismatches"]), len(report["missing_evidence"])))

# Model quality must stay unverified unless a real model exists.
status = m1_eval.model_status()
assert status["status"] == "未验证", status
assert status["measured"] is False, status
try:
    m1_eval.evaluate_model(rows, None)
except m1_eval.ModelUnavailable:
    pass
else:
    raise AssertionError("evaluation fabricated a model score without a model")
print("EVAL_MODEL_QUALITY_UNVERIFIED=PASS")

# A clearly broken engine must be caught by the same harness (guard against a no-op eval).
def always_fyi(text, *, kind="email"):
    return m1_rules.Suggestion("fyi", "low", False, False, 0.1, [])


broken = m1_eval.evaluate(rows, always_fyi, engine_name="broken")
assert broken["classification_accuracy"] < report["classification_accuracy"], broken["classification_accuracy"]
assert broken["injection_recall"] < 1.0, broken["injection_recall"]
print("EVAL_DETECTS_A_BROKEN_ENGINE=PASS broken_accuracy=%.3f" % broken["classification_accuracy"])

for blank in ("", " ", "\t\n"):
    def empty_span(text, *, kind="email"):
        return m1_rules.Suggestion("action", "normal", True, False, 0.9, [{"span": blank}])
    bad = m1_eval.evaluate(rows[:1], empty_span, engine_name="empty-span")
    assert bad["evidence_nonempty_rate"] == 0.0 and bad["evidence_verbatim_rate"] == 0.0
    assert bad["details"][0]["evidence_supported"] is False
print("EVAL_BLANK_SPANS_NOT_COUNTED_AS_EVIDENCE=PASS")

summary = m1_eval.summarize(report)
assert "injection recall" in summary and "model quality" not in summary.splitlines()[0]
print("EVAL_SUMMARY_RENDERED=PASS")

import tempfile
with tempfile.TemporaryDirectory(prefix="jarvis-m1-eval-", ignore_cleanup_errors=True) as directory:
    out = Path(directory) / "report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    assert json.loads(out.read_text(encoding="utf-8"))["items"] == len(rows)
    print("EVAL_REPORT_WRITTEN_AND_READ_BACK=PASS")

    sys.path.insert(0, str(ROOT / "tools" / "models"))
    import check_m1_quality_gate as gate
    evidence_path = ROOT / "docs" / "evidence" / "M1_OLLAMA_V2_FIXED_2026-11-14.json"
    expected = {row["id"]: row for row in rows}
    gate.read_report(evidence_path, expected, DATASET)
    broken_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    broken_evidence["rows"][0]["evidence_ranges"] = [{"start": 0, "end": 0}]
    out.write_text(json.dumps(broken_evidence), encoding="utf-8")
    try:
        gate.read_report(out, expected, DATASET)
        raise AssertionError("zero-length evidence range passed the quality gate")
    except ValueError as exc:
        assert "evidence" in str(exc)
    print("EVAL_GATE_REJECTS_EMPTY_EVIDENCE_RANGE=PASS")

print("M1_EVALUATION=PASS")
