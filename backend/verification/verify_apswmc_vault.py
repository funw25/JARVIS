"""Check experimental SQLite3MC + DPAPI vault with fictional data only."""
import hashlib
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
candidate = ROOT / ".jarvis-python314-apswmc-probe"
if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not candidate.is_dir():
    print("APSWMC_VAULT=NOT_RUN (isolated Windows CPython 3.14 candidate required)")
    raise SystemExit(2)
sys.path.insert(0, str(candidate))
sys.path.insert(0, str(ROOT / "backend"))
try:
    import apsw
except (ImportError, OSError):
    print("APSWMC_VAULT=NOT_RUN (candidate unavailable)")
    raise SystemExit(2)
from app.security.storage_policy import StorageSecurityError
from experimental import apswmc_synthetic_vault as vault

MARKER = "JARVIS_FICTIONAL_APSWMC_CONTENT"


def rejects(call, error):
    try:
        call()
    except error:
        return
    raise AssertionError("Expected " + error.__name__)


if len(sys.argv) == 3 and sys.argv[1] == "--child":
    with vault.open_vault(Path(sys.argv[2])) as conn:
        assert conn.execute("SELECT content FROM fictional_records WHERE id='sample'").fetchone() == (MARKER,)
    print("APSWMC_VAULT_CHILD=PASS")
    raise SystemExit(0)


with tempfile.TemporaryDirectory(prefix="jarvis-apswmc-vault-test-") as directory:
    root = Path(directory)
    target = root / "vault"
    os.environ.pop("JARVIS_DATA_MODE", None)
    rejects(lambda: vault.initialize(target), StorageSecurityError)
    assert not target.exists()
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    vault.initialize(target)
    original_hash = hashlib.sha256((target / "vault.db").read_bytes()).hexdigest()
    rejects(lambda: vault.initialize(target), vault.ExperimentalVaultError)
    assert hashlib.sha256((target / "vault.db").read_bytes()).hexdigest() == original_hash
    old_protect = vault.protect_synthetic_key
    try:
        def broken(_):
            raise OSError("fictional DPAPI failure")
        vault.protect_synthetic_key = broken
        rejects(lambda: vault.initialize(root / "failed"), OSError)
    finally:
        vault.protect_synthetic_key = old_protect
    assert not (root / "failed").exists()
    assert not any(item.name.startswith(".jarvis-apswmc-staging-") for item in root.iterdir())
    with vault.open_vault(target) as conn:
        conn.execute("INSERT INTO fictional_records(id,content) VALUES ('sample',?)", (MARKER,))
    with closing(sqlite3.connect(target / "vault.db")) as plain:
        rejects(lambda: plain.execute("SELECT COUNT(*) FROM sqlite_master").fetchone(), sqlite3.DatabaseError)
    rejects(lambda: vault._open(target / "vault.db", b"x" * 32, create=False), apsw.Error)
    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(target)], capture_output=True, text=True, timeout=30, env=dict(os.environ))
    assert child.returncode == 0 and child.stdout.strip() == "APSWMC_VAULT_CHILD=PASS"
    try:
        with vault.open_vault(target) as conn:
            conn.execute("INSERT INTO fictional_records(id,content) VALUES ('rollback','fictional')")
            raise RuntimeError("simulate failure")
    except RuntimeError:
        pass
    with vault.open_vault(target) as conn:
        assert conn.execute("SELECT COUNT(*) FROM fictional_records WHERE id='rollback'").fetchone() == (0,)
    key_path = target / "key.dpapi"
    wrapped = key_path.read_bytes()
    bad = bytearray(wrapped)
    bad[-1] ^= 1
    key_path.write_bytes(bad)
    before = hashlib.sha256((target / "vault.db").read_bytes()).hexdigest()
    rejects(lambda: vault.open_vault(target).__enter__(), vault.ExperimentalVaultError)
    assert hashlib.sha256((target / "vault.db").read_bytes()).hexdigest() == before
    key_path.write_bytes(wrapped)
    with vault.open_vault(target) as conn:
        assert conn.execute("SELECT content FROM fictional_records WHERE id='sample'").fetchone() == (MARKER,)
    real_driver = vault.apsw
    try:
        vault.apsw = sqlite3
        rejects(lambda: vault.initialize(root / "plain-driver"), vault.ExperimentalVaultError)
        assert not (root / "plain-driver").exists()
    finally:
        vault.apsw = real_driver
    assert MARKER.encode() not in (target / "vault.db").read_bytes()
    print("SYNTHETIC_GATE_INITIALIZATION_AND_NO_OVERWRITE=PASS")
    print("DPAPI_RESTART_WRONG_KEY_AND_TRANSACTION_ROLLBACK=PASS")
    print("CORRUPT_KEY_AND_PLAINTEXT_DRIVER_FAIL_CLOSED=PASS")
print("APSWMC_SYNTHETIC_VAULT=PASS")
print("REAL_APP_BACKUP_RECOVERY_AND_SECURITY_REVIEW=NOT_TESTED")
