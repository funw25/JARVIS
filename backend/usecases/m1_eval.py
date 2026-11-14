"""M1 evaluation for deterministic rules and separately configured real models.

The report separates four things that are easy to blur together:
  * rule-baseline quality on the labeled set (measured),
  * evidence support (spans must appear verbatim),
  * safety behaviour (injection recall),
  * model quality (measured only when a real model engine is explicitly supplied).
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


def evaluate(rows, engine=None, *, engine_name=None, text_key="text", dataset_authored_with_rules=True):
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
    retry_items = 0
    retry_total = 0

    for row in rows:
        labels = row["labels"]
        item_started = time.perf_counter()
        try:
            predicted = engine(row[text_key], kind=row.get("kind", "email"))
        except Exception as exc:
            raise RuntimeError("evaluation failed at %s: %s" % (row["id"], exc)) from exc
        latency_ms = round((time.perf_counter() - item_started) * 1000, 1)
        attempts = getattr(predicted, "attempts", 1)
        retry_items += int(attempts > 1)
        retry_total += max(0, attempts - 1)
        gold = labels["classification"]
        supported = m1_rules.evidence_supported(row[text_key], predicted)
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
            "attempts": attempts,
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
        nonempty = bool(predicted.evidence) and all(
            isinstance(item, dict) and isinstance(item.get("span"), str) and item["span"].strip()
            for item in predicted.evidence
        )
        if not nonempty:
            missing_evidence.append(row["id"])
        if nonempty:
            evidence_nonempty += 1
            if supported:
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
        "dataset_authored_with_rules": dataset_authored_with_rules,
        "generalization_claimed": False,
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "retry_items": retry_items,
        "retry_total": retry_total,
    }


def _accuracy(confusion, correct_key=None):
    correct = sum(counts.get(gold, 0) for gold, counts in confusion.items())
    total = sum(sum(counts.values()) for counts in confusion.values())
    return _safe_div(correct, total)


def evaluation_issues(report):
    """Derive per-item issues from ``details``, the canonical item-level record.

    Legacy mismatch lists remain in serialized reports for compatibility. Evidence
    that exists but is unsupported is an issue even when it is not "missing".
    """
    issues = []
    for item in report["details"]:
        gold, predicted = item["gold"], item["predicted"]
        for field in ("classification", "todo", "draft", "priority"):
            if gold[field] != predicted[field]:
                issues.append({"id": item["id"], "type": field, "gold": gold[field], "predicted": predicted[field]})
        if not item["evidence_supported"]:
            issues.append({"id": item["id"], "type": "evidence_unsupported"})
    return issues


def evaluate_model(rows, model, *, dataset_authored_with_rules=True):
    """Evaluate a real model, or refuse to fabricate a number."""
    if model is None:
        raise ModelUnavailable("no model is configured; model quality stays unverified")
    return evaluate(rows, model, engine_name=getattr(model, "name", "model"),
                    dataset_authored_with_rules=dataset_authored_with_rules)


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
    classification_issues = [issue for issue in evaluation_issues(report) if issue["type"] == "classification"]
    if classification_issues:
        lines.append("misclassified: " + ", ".join("%s %s->%s" % (f["id"], f["gold"], f["predicted"])
                                                    for f in classification_issues[:8]))
    else:
        lines.append("misclassified: none")
    return "\n".join(lines)


def model_status():
    return {
        "status": "未验证",
        "reason": "no real model provider is configured for M1; local_fake only proves the flow",
        "measured": False,
    }
