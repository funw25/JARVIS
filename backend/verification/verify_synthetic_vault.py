"""Verify experimental DPAPI + SQLCipher vault with fictional data only."""
import hashlib
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
if sys.platform != "win32" or importlib.util.find_spec("sqlcipher3") is None:
    print("SYNTHETIC_VAULT_VERIFICATION=NOT_RUN (Windows and SQLCipher are required)")
    raise SystemExit(2)
from app.security.storage_policy import StorageSecurityError
from experimental import synthetic_vault as vault

MARKER = "JARVIS_FICTIONAL_VAULT_CONTENT_2026"


def fails(call, error):
    try:
        call()
    except error:
        return
    raise AssertionError("Expected failure did not occur")


if len(sys.argv) == 3 and sys.argv[1] == "--child":
    with vault.open_synthetic_vault(Path(sys.argv[2])) as conn:
        result = conn.execute("SELECT content FROM fictional_records WHERE id='sample'").fetchone()
        assert result and result[0] == MARKER
    print("RESTART_REOPEN=PASS")
    raise SystemExit(0)


with tempfile.TemporaryDirectory(prefix="jarvis-synthetic-vault-test-") as directory:
    root = Path(directory)
    target = root / "vault"
    os.environ.pop("JARVIS_DATA_MODE", None)
    fails(lambda: vault.initialize_synthetic_vault(target), StorageSecurityError)
    assert not target.exists()
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    expected_sql = 'PRAGMA key = "x' + chr(39) + "00" * 32 + chr(39) + '"'
    assert vault._key_sql(bytes(32)) == expected_sql
    assert vault._key_sql(bytes([1]) * 32) != vault._key_sql(bytes(32))
    vault.initialize_synthetic_vault(target)
    before = hashlib.sha256((target / "vault.db").read_bytes()).hexdigest()
    fails(lambda: vault.initialize_synthetic_vault(target), vault.SyntheticVaultError)
    assert hashlib.sha256((target / "vault.db").read_bytes()).hexdigest() == before
    print("FAIL_CLOSED_CREATION_AND_NO_OVERWRITE=PASS")
    original_protect = vault.protect_synthetic_key
    def broken_protect(_):
        raise OSError("simulated key protection failure")
    try:
        vault.protect_synthetic_key = broken_protect
        fails(lambda: vault.initialize_synthetic_vault(root / "failed-init"), OSError)
    finally:
        vault.protect_synthetic_key = original_protect
    assert not (root / "failed-init").exists()
    assert not any(item.name.startswith(".jarvis-synthetic-vault-") for item in root.iterdir())
    print("INITIALIZATION_FAILURE_CLEANS_STAGING=PASS")

    with vault.open_synthetic_vault(target) as conn:
        conn.execute("INSERT INTO fictional_records(id,content) VALUES ('sample',?)", (MARKER,))
        conn.commit()
        wal = Path(str(target / "vault.db") + "-wal")
        assert wal.exists() and wal.stat().st_size > 0
        assert MARKER.encode() not in wal.read_bytes()
        assert MARKER.encode() not in (target / "vault.db").read_bytes()
    with closing(sqlite3.connect(target / "vault.db")) as plain:
        fails(lambda: plain.execute("SELECT count(*) FROM sqlite_master").fetchone(), sqlite3.DatabaseError)
    fails(lambda: vault._open_database(target / "vault.db", b"x" * 32, create=False), vault.cipher.DatabaseError)
    with vault.open_synthetic_vault(target) as conn:
        assert conn.execute("SELECT content FROM fictional_records WHERE id='sample'").fetchone()[0] == MARKER
    print("DATABASE_WAL_PLAINTEXT_AND_WRONG_KEY_CHECKS=PASS")

    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(target)],
                           capture_output=True, text=True, timeout=30, env=dict(os.environ))
    assert child.returncode == 0 and "RESTART_REOPEN=PASS" in child.stdout, child.stderr
    try:
        with vault.open_synthetic_vault(target) as conn:
            conn.execute("INSERT INTO fictional_records(id,content) VALUES ('rollback','fictional')")
            raise RuntimeError("simulate application failure")
    except RuntimeError:
        pass
    with vault.open_synthetic_vault(target) as conn:
        assert conn.execute("SELECT count(*) FROM fictional_records WHERE id='rollback'").fetchone()[0] == 0
    print("RESTART_AND_TRANSACTION_ROLLBACK=PASS")

    os.environ.pop("JARVIS_DATA_MODE", None)
    fails(lambda: vault.open_synthetic_vault(target).__enter__(), StorageSecurityError)
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    wrapped_path = target / "key.dpapi"
    original = wrapped_path.read_bytes()
    corrupted = bytearray(original)
    corrupted[-1] ^= 1
    wrapped_path.write_bytes(corrupted)
    database_hash = hashlib.sha256((target / "vault.db").read_bytes()).hexdigest()
    fails(lambda: vault.open_synthetic_vault(target).__enter__(), vault.SyntheticVaultError)
    assert hashlib.sha256((target / "vault.db").read_bytes()).hexdigest() == database_hash
    wrapped_path.write_bytes(original)
    with vault.open_synthetic_vault(target) as conn:
        assert conn.execute("SELECT count(*) FROM fictional_records").fetchone()[0] == 1
    real_driver = vault.cipher
    try:
        vault.cipher = sqlite3
        fails(lambda: vault.initialize_synthetic_vault(root / "fake-driver"), vault.SyntheticVaultError)
        assert not (root / "fake-driver").exists()
    finally:
        vault.cipher = real_driver
    print("CORRUPT_KEY_AND_PLAINTEXT_DRIVER_REJECTED=PASS")
print("SYNTHETIC_VAULT_VERIFICATION=PASS")
print("RECOVERY_BACKUP_USER_SESSIONS_AND_SECURITY_AUDIT=NOT_TESTED")
