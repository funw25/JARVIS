"""Run the whole verification suite against the experimental encrypted adapter.

Starts ``verify_all.py`` as a child process with ``JARVIS_STORAGE_ADAPTER=apswmc``
and a fresh random key, so every domain, migration, HTTP and concurrency check
runs on ciphertext. Synthetic temporary data only; the default storage is not
changed and no security review is implied.
"""
import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".jarvis-python314-apswmc-probe"

if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not PROBE.is_dir():
    print("APSWMC_FULL_SUITE=NOT_RUN (isolated Windows CPython 3.14 candidate required)")
    raise SystemExit(2)

with tempfile.TemporaryDirectory(prefix="jarvis-adapter-suite-", ignore_cleanup_errors=True) as directory:
    environment = dict(os.environ)
    environment.update({
        "JARVIS_DATA_MODE": "synthetic",
        "JARVIS_DATA_DIR": str(Path(directory) / "data"),
        "JARVIS_STORAGE_ADAPTER": "apswmc",
        "JARVIS_APSWMC_KEY": secrets.token_bytes(32).hex(),
        "PYTHONIOENCODING": "utf-8",
    })
    run = subprocess.run(
        [sys.executable, str(ROOT / "backend" / "verification" / "verify_all.py")],
        cwd=ROOT, env=environment, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=1800,
    )

    lines = (run.stdout or "").splitlines()
    summary = [line for line in lines if line.startswith("Summary:")]
    failures = [line for line in lines if line.startswith("FAIL ")]
    skipped = [line for line in lines if line.startswith("SKIP ")]
    print(summary[-1] if summary else "Summary: (missing)")
    for line in failures:
        print(line)
    for line in skipped:
        print(line)
    if run.returncode != 0 or failures:
        raise SystemExit(1)

print("APSWMC_FULL_SUITE=PASS")
print("DEFAULT_STORAGE_UNCHANGED=TRUE")
print("BACKUP_RECOVERY_AND_SECURITY_REVIEW=NOT_TESTED")
