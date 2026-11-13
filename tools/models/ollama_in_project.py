"""Run the pinned Ollama CLI with process-local configuration under the project.

Ollama's model directory setting does not move its first-run signing key. On
Windows, the CLI derives that key path from USERPROFILE. Override it only in
the Ollama child environment; the invoking shell and other processes keep their
normal profile. No sign-in or cloud model is used.
"""
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".jarvis-ollama"
CLI = BASE / "runtime-v0.40.15" / "ollama.exe"
PROFILE = BASE / "isolated-profile"
MODELS = BASE / "models"


def main(argv):
    if not CLI.is_file():
        raise SystemExit("verified Ollama CLI is missing")
    if not argv or argv[0] not in ("serve", "pull", "list", "show", "--version"):
        raise SystemExit("usage: ollama_in_project.py serve|pull|list|show|--version [args]")
    if argv[0] == "pull" and (len(argv) != 2 or argv[1].lower().endswith((":cloud", "-cloud"))):
        raise SystemExit("pull requires one non-cloud model tag")

    PROFILE.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)
    child_env = os.environ.copy()
    child_env["USERPROFILE"] = str(PROFILE)
    child_env["OLLAMA_MODELS"] = str(MODELS)
    child_env["OLLAMA_NO_CLOUD"] = "1"
    child_env["OLLAMA_HOST"] = "127.0.0.1:11434"
    child_env["HTTPS_PROXY"] = "http://127.0.0.1:7890"
    child_env["NO_PROXY"] = "127.0.0.1,localhost"

    print("OLLAMA_CHILD_PROFILE=%s command=%s cloud_disabled=1" % (PROFILE, argv[0]), flush=True)
    return subprocess.call([str(CLI), *argv], cwd=BASE, env=child_env)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
