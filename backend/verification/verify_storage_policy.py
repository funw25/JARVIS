"""Synthetic storage gate verification; no personal data or network."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.database import connect, StorageAdapterError
from app.security.storage_policy import StorageSecurityError


with tempfile.TemporaryDirectory(prefix="jarvis-storage-check-") as directory:
    path = Path(directory) / "not-created" / "test.sqlite3"
    for mode in (None, "personal", "production", "invalid"):
        if mode is None:
            os.environ.pop("JARVIS_DATA_MODE", None)
        else:
            os.environ["JARVIS_DATA_MODE"] = mode
        try:
            with connect(path):
                raise AssertionError("blocked mode unexpectedly opened a database")
        except StorageSecurityError:
            pass
        assert not path.exists() and not path.parent.exists()
    print("UNSET_AND_NON_SYNTHETIC_FAIL_CLOSED=PASS")
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_STORAGE_ADAPTER"] = "typo-cannot-fall-back"
    try:
        connect(path)
        raise AssertionError("unknown storage adapter fell back to plaintext")
    except StorageAdapterError:
        pass
    assert not path.exists() and not path.parent.exists()
    os.environ.pop("JARVIS_STORAGE_ADAPTER")
    print("UNKNOWN_ADAPTER_FAILS_CLOSED=PASS")
    with connect(path) as conn:
        conn.execute("CREATE TABLE synthetic_example(id INTEGER PRIMARY KEY)")
    assert path.exists()
    print("EXPLICIT_SYNTHETIC_MODE=PASS")

print("STORAGE_GUARD_VERIFICATION=PASS")
