"""Run the synthetic M1 comparison against the pinned local Ollama model."""
import argparse
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PYTHON = ROOT / ".jarvis-python-3.14.10" / "python.exe"
CLI = ROOT / "backend" / "usecases" / "m1_cli.py"
RESULTS = ROOT / ".jarvis-ollama" / "results"
MODEL = "qwen3:4b-instruct-2507-q4_K_M"
PILOT_IDS = "m1-001,m1-015,m1-027,m1-035,m1-041"


def main():
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--pilot", action="store_true", help="test one fictional row from each of the five classes")
    selection.add_argument("--ids", help="comma-separated fictional dataset IDs for a focused experiment")
    parser.add_argument("--out", type=Path, help="report path; defaults to the ignored project results directory")
    parser.add_argument("--prompt-version", choices=("v1", "v2"), default="v1")
    parser.add_argument("--holdout", action="store_true", help="evaluate the separate fictional holdout")
    args = parser.parse_args()
    if not PYTHON.is_file():
        parser.error("pinned Python 3.14.10 is missing")
    RESULTS.mkdir(parents=True, exist_ok=True)
    name = ("holdout.json" if args.holdout else
            "pilot-five-classes.json" if args.pilot else ("selected.json" if args.ids else "full.json"))
    output = args.out or RESULTS / name
    output.parent.mkdir(parents=True, exist_ok=True)

    child_env = os.environ.copy()
    child_env["JARVIS_M1_MODEL_PROVIDER"] = "ollama"
    child_env["JARVIS_M1_MODEL"] = MODEL
    child_env["JARVIS_M1_MODEL_BASE_URL"] = "http://127.0.0.1:11434"
    child_env["JARVIS_M1_MODEL_TIMEOUT"] = "180"
    child_env["JARVIS_M1_PROMPT_VERSION"] = args.prompt_version
    command = [str(PYTHON), str(CLI), "eval", "--engine", "model", "--out", str(output)]
    if args.pilot:
        command.extend(("--ids", PILOT_IDS))
    elif args.ids:
        command.extend(("--ids", args.ids))
    if args.holdout:
        command.append("--holdout")
    print("M1_LOCAL_RUN model=%s prompt=%s output=%s" % (MODEL, args.prompt_version, output), flush=True)
    return subprocess.call(command, cwd=ROOT, env=child_env)


if __name__ == "__main__":
    raise SystemExit(main())
