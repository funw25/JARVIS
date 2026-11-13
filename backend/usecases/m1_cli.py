"""CLI for the M1 offline loop (fictional data only).

Examples:
    python usecases/m1_cli.py eval
    python usecases/m1_cli.py demo --id m1-001
    python usecases/m1_cli.py demo --id m1-005 --modify '{"todo": false, "title": "自定义标题"}'
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("JARVIS_DATA_MODE", "synthetic")

from usecases import m1_eval, m1_model, m1_pipeline, m1_rules  # noqa: E402

DATASET = BACKEND / "evals" / "m1" / "artifacts.jsonl"
HOLDOUT = BACKEND / "evals" / "m1" / "holdout.jsonl"


def _rows():
    return m1_pipeline.load_dataset(DATASET)


def command_eval(args):
    holdout = bool(getattr(args, "holdout", False))
    rows = m1_pipeline.load_dataset(HOLDOUT) if holdout else _rows()
    dataset_total = len(rows)
    requested_ids = getattr(args, "ids", None)
    if requested_ids:
        ids = [item.strip() for item in requested_ids.split(",") if item.strip()]
        by_id = {row["id"]: row for row in rows}
        if not ids or len(ids) != len(set(ids)) or any(item not in by_id for item in ids):
            print("error: --ids must contain distinct known dataset IDs", file=sys.stderr)
            return 2
        rows = [by_id[item] for item in ids]
    if getattr(args, "limit", None) is not None:
        if not 1 <= args.limit <= len(rows):
            print("error: --limit must be between 1 and %d" % len(rows), file=sys.stderr)
            return 2
        rows = rows[:args.limit]
    engine = None
    config = None
    model_probe = None
    baseline = None
    if getattr(args, "engine", "rules") == "model":
        try:
            config = m1_model.load_config()
            engine = m1_model.engine(config)
            if not m1_model.is_loopback_endpoint(config) and not getattr(args, "allow_remote", False):
                print("error: remote model endpoint requires --allow-remote after reviewing the input and budget",
                      file=sys.stderr)
                return 2
            if config.provider == "ollama":
                model_probe = m1_model.probe(config)
                if not model_probe["available"]:
                    print("error: requested Ollama model is not confirmed as a downloaded local model",
                          file=sys.stderr)
                    return 2
        except m1_model.ModelUnavailable as exc:
            print("error: %s" % exc, file=sys.stderr)
            print("model quality: 未验证 (no real model reachable)", file=sys.stderr)
            return 2
    if engine is None:
        report = m1_eval.evaluate(rows, dataset_authored_with_rules=not holdout)
        model_state = m1_eval.model_status()
    else:
        report = m1_eval.evaluate_model(rows, engine, dataset_authored_with_rules=not holdout)
        baseline = m1_eval.evaluate(rows, dataset_authored_with_rules=not holdout)
        report["model_config"] = config.describe()
        if model_probe is not None:
            report["model_digest"] = model_probe["model_digest"]
            report["model_size_bytes"] = model_probe["model_size_bytes"]
        model_state = {
            "status": "候选模型输出已测量",
            "measured": True,
            "provider_identity_verified": False,
            "config": config.describe(),
        }
        if model_probe is not None:
            model_state["model_digest"] = model_probe["model_digest"]
    print(m1_eval.summarize(report))
    print("model quality: %s" % model_state["status"])
    if len(rows) < dataset_total:
        print("partial run: %d/%d items; not an M1 completion result" % (len(rows), dataset_total))
    if args.out:
        payload = {"report": report, "model": model_state,
                   "dataset_id": "m1-holdout-v1" if holdout else "m1-fixed-v1",
                   "dataset_total": dataset_total, "complete_dataset": len(rows) == dataset_total}
        if baseline is not None:
            payload["rule_report"] = baseline
            rules_by_id = {item["id"]: item for item in baseline["details"]}
            payload["comparison"] = [
                {"id": item["id"], "gold": item["gold"],
                 "rules": rules_by_id[item["id"]]["predicted"],
                 "model": item["predicted"],
                 "model_evidence_supported": item["evidence_supported"],
                 "model_latency_ms": item["latency_ms"]}
                for item in report["details"]
            ]
        Path(args.out).write_text(json.dumps(payload,
                                             indent=2, ensure_ascii=False), encoding="utf-8")
        print("written: %s" % args.out)
    return 0


def command_demo(args):
    rows = {row["id"]: row for row in _rows()}
    if args.id not in rows:
        print("unknown id; available: %s" % ", ".join(sorted(rows)[:6]))
        return 2
    edits = json.loads(args.modify) if args.modify else None
    import app.database as database

    with tempfile.TemporaryDirectory(prefix="jarvis-m1-demo-", ignore_cleanup_errors=True) as directory:
        os.environ["JARVIS_DATA_DIR"] = directory
        database.apply_migrations()
        with database.connect() as connection:
            transcript = m1_pipeline.run_item(connection, rows[args.id], action=args.action, edits=edits)
            print("== artifact ==")
            print(rows[args.id]["text"])
            print("== suggestion ==")
            print(json.dumps(transcript["suggestion"], indent=2, ensure_ascii=False))
            print("== decision ==")
            print(json.dumps(transcript["decision"], ensure_ascii=False))
            print("== created ==")
            print(json.dumps(transcript["created"], ensure_ascii=False))
            print("== audit ==")
            for event in transcript["audit"]:
                print("  %s -> %s" % (event["event_type"], event["object_type"]))
            todo = connection.execute("SELECT title, priority, status FROM todos WHERE id=?",
                                      (transcript["created"]["todo_id"],)).fetchone() if transcript["created"]["todo_id"] else None
            draft = connection.execute("SELECT subject, body FROM m1_drafts WHERE id=?",
                                       (transcript["created"]["draft_id"],)).fetchone() if transcript["created"]["draft_id"] else None
            if todo:
                print("== todo row ==")
                print(dict(todo))
            if draft:
                print("== draft row ==")
                print(draft["subject"])
                print(draft["body"])
    return 0


def command_status(args):
    print("dataset: %s" % DATASET)
    print("items: %d" % len(_rows()))
    print("engine: %s" % m1_rules.ENGINE_NAME)
    print("model: %s" % m1_eval.model_status()["status"])
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Jarvis M1 offline vertical loop")
    sub = parser.add_subparsers(dest="command", required=True)

    evaluate = sub.add_parser("eval", help="evaluate the rule baseline on the labeled set")
    evaluate.add_argument("--out", help="write the full report as JSON")
    evaluate.add_argument("--engine", choices=["rules", "model"], default="rules",
                         help="rules = deterministic baseline; model = configured real model")
    evaluate.add_argument("--allow-remote", action="store_true",
                          help="explicitly permit sending the fictional evaluation set to a non-local endpoint")
    evaluate.add_argument("--holdout", action="store_true",
                          help="evaluate the separate 20-item fictional holdout instead of the fixed 53-item set")
    selection = evaluate.add_mutually_exclusive_group()
    selection.add_argument("--limit", type=int,
                           help="pilot on the first N fictional rows; partial results cannot complete M1")
    selection.add_argument("--ids", help="pilot on comma-separated fictional dataset IDs")
    evaluate.set_defaults(func=command_eval)

    demo = sub.add_parser("demo", help="run one artifact through the whole loop")
    demo.add_argument("--id", required=True)
    demo.add_argument("--action", default="accept", choices=["accept", "modify", "reject"])
    demo.add_argument("--modify", help="JSON edits, e.g. {\"todo\": false}")
    demo.set_defaults(func=command_demo)

    status = sub.add_parser("status", help="show dataset and engine status")
    status.set_defaults(func=command_status)

    arguments = parser.parse_args(argv)
    return arguments.func(arguments) or 0


if __name__ == "__main__":
    raise SystemExit(main())
