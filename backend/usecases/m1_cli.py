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

from usecases import m1_eval, m1_pipeline, m1_rules  # noqa: E402

DATASET = BACKEND / "evals" / "m1" / "artifacts.jsonl"


def _rows():
    return m1_pipeline.load_dataset(DATASET)


def command_eval(args):
    report = m1_eval.evaluate(_rows())
    print(m1_eval.summarize(report))
    print("model quality: %s (%s)" % (m1_eval.model_status()["status"], m1_eval.model_status()["reason"]))
    if args.out:
        Path(args.out).write_text(json.dumps({"report": report, "model": m1_eval.model_status()},
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
