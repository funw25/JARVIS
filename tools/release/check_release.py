"""Fail-closed entry point for a future real-data release; does not publish."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "backend" / "verification" / "verify_driver_provenance.py"


def main():
    result = subprocess.run([sys.executable, str(CHECK), "--release"], cwd=ROOT)
    if result.returncode:
        print("RELEASE_BLOCKED=G1", flush=True)
        return 1
    # No production release pipeline or independently verified G2/G3/G4/G5 exists yet.
    # Do not make G1 approval alone enable distribution or personal data.
    print("RELEASE_BLOCKED=OTHER_REAL_DATA_GATES_NOT_IMPLEMENTED", flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
