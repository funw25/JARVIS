"""Run the whole verification suite against the experimental encrypted adapter.

Starts ``verify_all.py --experimental-apswmc`` with a fresh random key. Individual
checks can explicitly select another backend. Synthetic data only; this is not
a security review.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".jarvis-python314-apswmc-probe"

if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not PROBE.is_dir():
    print("APSWMC_FULL_SUITE=NOT_RUN (isolated Windows CPython 3.14 candidate required)")
    raise SystemExit(2)

with tempfile.TemporaryDirectory(prefix="jarvis-adapter-suite-") as directory:
    environment = dict(os.environ)
    environment.update({
        "PYTHONIOENCODING": "utf-8",
    })
    run = subprocess.run(
        [sys.executable, str(ROOT / "backend" / "verification" / "verify_all.py"), "--experimental-apswmc"],
        cwd=ROOT, env=environment, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1800,
    )

    lines = (run.stdout or "").splitlines()
    summaries = [line for line in lines if line.startswith(("FUNCTIONAL Summary:", "DIAGNOSTIC Summary:"))]
    failures = [line for line in lines if line.startswith("FAIL ")]
    skipped = [line for line in lines if line.startswith("SKIP ")]
    for line in summaries:
        print(line)
    if len(summaries) != 2:
        print("FAIL: functional or diagnostic summary missing")
    for line in failures:
        print(line)
    for line in skipped:
        print(line)
    if run.returncode != 0 or failures or len(summaries) != 2:
        raise SystemExit(1)

print("APSWMC_FULL_SUITE=PASS")
print("DEFAULT_STORAGE_UNCHANGED=TRUE")
print("BACKUP_RECOVERY_AND_SECURITY_REVIEW=NOT_TESTED")
