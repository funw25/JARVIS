"""Export a compact, text-free record from a complete fictional M1 model run."""
import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATASETS = {
    "fixed": ROOT / "backend" / "evals" / "m1" / "artifacts.jsonl",
    "holdout": ROOT / "backend" / "evals" / "m1" / "holdout.jsonl",
}
FIELDS = ("classification", "priority", "todo", "draft")
METRICS = ("items", "classification_accuracy", "todo_accuracy", "draft_accuracy",
           "priority_accuracy", "evidence_nonempty_rate", "evidence_verbatim_rate",
           "injection_recall", "duration_ms", "per_class", "confusion", "retry_items", "retry_total")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--dataset", choices=DATASETS, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--adapter-sha256", help="hash recorded at run time if the adapter changed since")
    args = parser.parse_args()
    dataset_path = DATASETS[args.dataset]
    dataset = {row["id"]: row for row in
               (json.loads(line) for line in dataset_path.read_text(encoding="utf-8").splitlines() if line.strip())}
    full = json.loads(args.report.read_text(encoding="utf-8"))
    report = full["report"]
    details = report["details"]
    if not full.get("complete_dataset") or full.get("dataset_total") != len(dataset):
        parser.error("the report does not cover the complete selected dataset")
    if report["items"] != len(dataset) or len(details) != len(dataset):
        parser.error("report item count does not match the selected dataset")
    if len({item["id"] for item in details}) != len(dataset):
        parser.error("report IDs are duplicated")
    rows = []
    for item in details:
        expected = dataset.get(item["id"])
        if expected is None or any(item["gold"].get(field) != expected["labels"][field] for field in FIELDS):
            parser.error("report gold labels differ from the selected dataset")
        rows.append({
            "id": item["id"],
            "gold": {field: item["gold"][field] for field in FIELDS},
            "predicted": {field: item["predicted"][field] for field in FIELDS},
            "evidence_supported": item["evidence_supported"],
            "latency_ms": item["latency_ms"],
            "attempts": item.get("attempts", 1),
        })
    payload = {
        "scope": "fictional M1 samples only; raw message text omitted",
        "dataset_id": full.get("dataset_id", "m1-fixed-v1" if args.dataset == "fixed" else "m1-holdout-v1"),
        "dataset_total": len(dataset),
        "dataset_sha256": sha256(dataset_path),
        "adapter_sha256": args.adapter_sha256 or sha256(ROOT / "backend" / "usecases" / "m1_model.py"),
        "full_report_sha256": sha256(args.report),
        "model": report["model_config"],
        "model_digest": report["model_digest"],
        "model_size_bytes": report.get("model_size_bytes"),
        "complete_dataset": True,
        "metrics": {key: report[key] for key in METRICS if key in report},
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("M1_EVIDENCE_EXPORTED dataset=%s rows=%d output=%s" %
          (args.dataset, len(rows), args.out))


if __name__ == "__main__":
    main()
