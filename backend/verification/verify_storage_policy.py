"""Synthetic storage gate verification; no personal data or network."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app.database import connect, StorageAdapterError
from app.security.storage_policy import StorageSecurityError
from test_environment import isolated_environment

hostile = {"JARVIS_VAULT_DIR": "do-not-touch", "JARVIS_STORAGE_ADAPTER": "dpapi-vault",
           "JARVIS_BACKUP_PASSPHRASE": "fictional-secret", "KEEP_THIS": "yes"}
clean = isolated_environment(hostile)
assert clean == {"KEEP_THIS": "yes"}
adapter = isolated_environment(hostile, experimental_apswmc=True)
assert adapter["JARVIS_STORAGE_ADAPTER"] == "apswmc" and len(adapter["JARVIS_APSWMC_KEY"]) == 64
assert "JARVIS_VAULT_DIR" not in adapter and "JARVIS_BACKUP_PASSPHRASE" not in adapter
assert adapter["JARVIS_APSWMC_KEY"] != isolated_environment(hostile, experimental_apswmc=True)["JARVIS_APSWMC_KEY"]
print("VERIFICATION_ENVIRONMENT_ISOLATION=PASS")


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
