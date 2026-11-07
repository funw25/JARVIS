"""Isolated fictional APSW SQLite3MC + DPAPI vault experiment.

Not an application storage backend. No backup, recovery, release policy or audit.
"""
import os
import secrets
import sys
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path

from app.security.storage_policy import require_synthetic_storage
from .windows_dpapi import protect_synthetic_key, unprotect_synthetic_key

try:
    import apsw
except (ImportError, OSError):
    apsw = None


class ExperimentalVaultError(RuntimeError):
    pass


def _driver():
    if sys.platform != "win32" or apsw is None or "Multiple Ciphers" not in getattr(apsw, "mc_version", ""):
        raise ExperimentalVaultError("Isolated SQLite3MC driver unavailable; no plaintext fallback")
    return apsw


def _open(path: Path, key: bytes, *, create: bool):
    driver = _driver()
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("Invalid experimental key")
    flags = driver.SQLITE_OPEN_READWRITE | (driver.SQLITE_OPEN_CREATE if create else 0)
    conn = driver.Connection(str(path), flags=flags)
    try:
        if conn.pragma("cipher", "chacha20") != "chacha20" or conn.pragma("key", "raw:" + key.hex()) != "ok":
            raise ExperimentalVaultError("Experimental encryption configuration failed")
        list(conn.execute("SELECT name FROM sqlite_master"))
        conn.pragma("temp_store", "MEMORY")
        conn.pragma("foreign_keys", 1)
        if conn.pragma("temp_store") != 2 or conn.pragma("foreign_keys") != 1:
            raise ExperimentalVaultError("Experimental storage settings unavailable")
        if not create:
            row = conn.execute("SELECT value FROM vault_meta WHERE name='format'").fetchone()
            if row != ("jarvis-apswmc-synthetic-v1",):
                raise ExperimentalVaultError("Unexpected experimental vault format")
        return conn
    except Exception:
        conn.close()
        raise


def initialize(target: Path) -> Path:
    require_synthetic_storage()
    _driver()
    target = Path(target)
    parent = target.parent.resolve(strict=True)
    target = parent / target.name
    if not parent.is_dir() or not target.name or os.path.lexists(target):
        raise ExperimentalVaultError("Experimental vault target already exists or is invalid")
    with tempfile.TemporaryDirectory(prefix=".jarvis-apswmc-staging-", dir=parent) as work:
        staging = Path(work)
        if staging.resolve().parent != parent:
            raise ExperimentalVaultError("Invalid staging directory")
        key = secrets.token_bytes(32)
        wrapped = protect_synthetic_key(key)
        with (staging / "key.dpapi").open("xb") as output:
            output.write(wrapped)
        database = staging / "vault.db"
        with closing(_open(database, key, create=True)) as conn:
            if str(conn.pragma("journal_mode", "WAL")).lower() != "wal":
                raise ExperimentalVaultError("Experimental WAL mode unavailable")
            with conn:
                conn.execute("CREATE TABLE vault_meta(name TEXT PRIMARY KEY,value TEXT NOT NULL)")
                conn.execute("INSERT INTO vault_meta(name,value) VALUES ('format','jarvis-apswmc-synthetic-v1')")
                conn.execute("CREATE TABLE fictional_records(id TEXT PRIMARY KEY,content TEXT NOT NULL)")
        with database.open("rb") as source:
            if source.read(16) == b"SQLite format 3\x00":
                raise ExperimentalVaultError("Plain database detected")
        if os.path.lexists(target):
            raise ExperimentalVaultError("Experimental vault target already exists")
        os.rename(staging, target)
    return target


@contextmanager
def open_vault(target: Path):
    require_synthetic_storage()
    driver = _driver()
    target = Path(target)
    database, key_file = target / "vault.db", target / "key.dpapi"
    if (target.is_symlink() or getattr(target, "is_junction", lambda: False)()
            or database.is_symlink() or key_file.is_symlink()
            or not target.is_dir() or not database.is_file() or not key_file.is_file()):
        raise ExperimentalVaultError("Experimental vault files are missing or unsupported")
    try:
        with key_file.open("rb") as source:
            wrapped = source.read(16385)
        if not 1 <= len(wrapped) <= 16384:
            raise ExperimentalVaultError("Invalid wrapped experimental key")
        key = unprotect_synthetic_key(wrapped)
        conn = _open(database, key, create=False)
    except (OSError, ValueError, driver.Error) as exc:
        raise ExperimentalVaultError("Experimental vault could not be unlocked") from exc
    try:
        with conn:
            yield conn
    finally:
        conn.close()
