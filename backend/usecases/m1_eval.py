"""Rule-baseline evaluation for M1. Model quality stays explicitly unverified.

The report separates four things that are easy to blur together:
  * rule-baseline quality on the labeled set (measured),
  * evidence support (spans must appear verbatim),
  * safety behaviour (injection recall),
  * model quality (NOT measured unless a real model is configured).
"""
import json
import time
from collections import Counter, defaultdict

from . import m1_rules

CLASSES = m1_rules.CLASSES


class ModelUnavailable(RuntimeError):
    """Raised when an evaluation asks for a model that is not configured."""


def _safe_div(value, total):
    return round(value / total, 4) if total else 0.0


def evaluate(rows, engine=None, *, engine_name=None, text_key="text"):
    engine = engine or m1_rules.suggest
    started = time.perf_counter()
    confusion = defaultdict(Counter)
    per_class = {name: {"tp": 0, "fp": 0, "fn": 0} for name in CLASSES}
    todo_correct = draft_correct = priority_correct = 0
    todo_mismatches = []
    priority_mismatches = []
    missing_evidence = []
    evidence_verbatim = evidence_nonempty = 0
    failures = []
    details = []

    for row in rows:
        labels = row["labels"]
        item_started = time.perf_counter()
        predicted = engine(row[text_key], kind=row.get("kind", "email"))
        latency_ms = round((time.perf_counter() - item_started) * 1000, 1)
        gold = labels["classification"]
        supported = bool(predicted.evidence) and all(
            item["span"] in row[text_key] for item in predicted.evidence
        )
        details.append({
            "id": row["id"],
            "gold": {key: labels[key] for key in ("classification", "priority", "todo", "draft")},
            "predicted": {
                "classification": predicted.classification,
                "priority": predicted.priority,
                "todo": bool(predicted.wants_todo),
                "draft": bool(predicted.wants_draft),
                "evidence": [item["span"] for item in predicted.evidence],
            },
            "evidence_supported": supported,
            "latency_ms": latency_ms,
        })
        confusion[gold][predicted.classification] += 1
        if predicted.classification == gold:
            per_class[gold]["tp"] += 1
        else:
            per_class[gold]["fn"] += 1
            per_class[predicted.classification]["fp"] += 1
            failures.append({
                "id": row["id"], "gold": gold, "predicted": predicted.classification,
                "note": row.get("note", ""),
            })
        if bool(predicted.wants_todo) != bool(labels["todo"]):
            todo_mismatches.append({"id": row["id"], "gold": labels["todo"], "predicted": bool(predicted.wants_todo)})
        todo_correct += int(bool(predicted.wants_todo) == bool(labels["todo"]))
        draft_correct += int(bool(predicted.wants_draft) == bool(labels["draft"]))
        if predicted.priority != labels["priority"]:
            priority_mismatches.append({"id": row["id"], "gold": labels["priority"], "predicted": predicted.priority})
        priority_correct += int(predicted.priority == labels["priority"])
        if not predicted.evidence:
            missing_evidence.append(row["id"])
        if predicted.evidence:
            evidence_nonempty += 1
            if all(item["span"] in row[text_key] for item in predicted.evidence):
                evidence_verbatim += 1

    total = len(rows)
    metrics = {}
    for name in CLASSES:
        tp, fp, fn = per_class[name]["tp"], per_class[name]["fp"], per_class[name]["fn"]
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, tp + fn)
        metrics[name] = {
            "precision": precision, "recall": recall,
            "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
            "support": sum(1 for row in rows if row["labels"]["classification"] == name),
        }

    return {
        "engine": engine_name or m1_rules.ENGINE_NAME,
        "items": total,
        "classification_accuracy": _accuracy(confusion),
        "todo_accuracy": _safe_div(todo_correct, total),
        "draft_accuracy": _safe_div(draft_correct, total),
        "priority_accuracy": _safe_div(priority_correct, total),
        "evidence_nonempty_rate": _safe_div(evidence_nonempty, total),
        "evidence_verbatim_rate": _safe_div(evidence_verbatim, total),
        "injection_recall": metrics["injection"]["recall"],
        "per_class": metrics,
        "confusion": {gold: dict(counts) for gold, counts in confusion.items()},
        "failures": failures,
        "details": details,
        "todo_mismatches": todo_mismatches,
        "priority_mismatches": priority_mismatches,
        "missing_evidence": missing_evidence,
        "dataset_authored_with_rules": True,
        "generalization_claimed": False,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
    }


def _accuracy(confusion, correct_key=None):
    correct = sum(counts.get(gold, 0) for gold, counts in confusion.items())
    total = sum(sum(counts.values()) for counts in confusion.values())
    return _safe_div(correct, total)


def evaluate_model(rows, model):
    """Evaluate a real model, or refuse to fabricate a number."""
    if model is None:
        raise ModelUnavailable("no model is configured; model quality stays unverified")
    return evaluate(rows, model, engine_name=getattr(model, "name", "model"))


def summarize(report):
    lines = [
        "engine: %s  items: %d  duration: %sms" % (report["engine"], report["items"], report["duration_ms"]),
        "classification accuracy: %.1f%%" % (report["classification_accuracy"] * 100),
        "todo accuracy: %.1f%%   draft accuracy: %.1f%%   priority accuracy: %.1f%%" % (
            report["todo_accuracy"] * 100, report["draft_accuracy"] * 100, report["priority_accuracy"] * 100),
        "evidence non-empty: %.1f%%   verbatim: %.1f%%" % (
            report["evidence_nonempty_rate"] * 100, report["evidence_verbatim_rate"] * 100),
        "injection recall: %.1f%%" % (report["injection_recall"] * 100),
    ]
    for name in CLASSES:
        item = report["per_class"][name]
        lines.append("  %-12s P=%.2f R=%.2f F1=%.2f (n=%d)" % (
            name, item["precision"], item["recall"], item["f1"], item["support"]))
    if report["failures"]:
        lines.append("misclassified: " + ", ".join("%s %s->%s" % (f["id"], f["gold"], f["predicted"])
                                                    for f in report["failures"][:8]))
    else:
        lines.append("misclassified: none")
    return "\n".join(lines)


def model_status():
    return {
        "status": "未验证",
        "reason": "no real model provider is configured for M1; local_fake only proves the flow",
        "measured": False,
    }
