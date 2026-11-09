"""Run only the mandatory, account-free M1 checks.

This script checks the synthetic dataset, vertical loop and rule evaluation.
It does not certify model quality or release readiness.
"""
import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CHECKS = (
    "verify_m1_dataset.py",
    "verify_m1_pipeline.py",
    "verify_m1_evaluation.py",
)


def main():
    if sys.version_info[:3] != (3, 14, 10):
        print("M1_RUNTIME=FAIL expected Python 3.14.10, got %s" % sys.version.split()[0])
        return 2

    env = os.environ.copy()
    env["JARVIS_DATA_MODE"] = "synthetic"
    for name in ("JARVIS_STORAGE_ADAPTER", "JARVIS_APSWMC_KEY", "JARVIS_VAULT_DIR"):
        env.pop(name, None)

    for filename in CHECKS:
        run = subprocess.run(
            [sys.executable, str(HERE / filename)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if run.returncode:
            print("FAIL %s (exit %d)" % (filename, run.returncode))
            print(run.stdout + run.stderr)
            return 1
        print("PASS %s" % filename)

    print("M1_OFFLINE_CHECKS=PASS count=%d skip=0" % len(CHECKS))
    print("M1_MODEL_QUALITY=UNVERIFIED")
    print("M1_EXIT=OPEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
