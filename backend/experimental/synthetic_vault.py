"""Experimental SQLCipher + DPAPI vault for fictional data only.

Not connected to Jarvis storage; no recovery, backup or production support.
"""
import os
import secrets
import sqlite3
import sys
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path

from app.security.storage_policy import require_synthetic_storage
from .windows_dpapi import protect_synthetic_key, unprotect_synthetic_key

try:
    from sqlcipher3 import dbapi2 as cipher
except (ImportError, OSError):
    cipher = None


class SyntheticVaultError(RuntimeError):
    pass


def _driver():
    if sys.platform != "win32":
        raise SyntheticVaultError("Synthetic vault requires Windows")
    if cipher is None:
        raise SyntheticVaultError("SQLCipher driver unavailable; no plaintext fallback")
    return cipher


def _key_sql(key: bytes) -> str:
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("Invalid synthetic vault key")
    return f'''PRAGMA key = "x'{key.hex()}'"'''


def _open_database(database: Path, key: bytes, *, create: bool):
    driver = _driver()
    location = str(database) if create else database.resolve(strict=True).as_uri() + "?mode=rw"
    conn = driver.connect(location, uri=not create)
    try:
        conn.execute(_key_sql(key))
        version = conn.execute("PRAGMA cipher_version").fetchone()
        if not version or not version[0]:
            raise SyntheticVaultError("SQLCipher not available; no plaintext fallback")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA foreign_keys=ON")
        if not create:
            row = conn.execute("SELECT value FROM vault_meta WHERE name='format'").fetchone()
            if row is None or row[0] != "jarvis-synthetic-vault-v1":
                raise SyntheticVaultError("Unsupported synthetic vault format")
        return conn
    except Exception:
        conn.close()
        raise


def initialize_synthetic_vault(target: Path) -> Path:
    """Create a new isolated vault; never overwrite an existing target."""
    require_synthetic_storage()
    _driver()
    target = Path(target)
    parent = target.parent.resolve(strict=True)
    target = parent / target.name
    if not target.name or os.path.lexists(target):
        raise SyntheticVaultError("Synthetic vault target already exists")
    if not parent.is_dir():
        raise SyntheticVaultError("Synthetic vault parent directory is unavailable")
    with tempfile.TemporaryDirectory(prefix=".jarvis-synthetic-vault-", dir=parent) as work:
        staging = Path(work)
        if staging.resolve().parent != parent:
            raise SyntheticVaultError("Invalid synthetic staging directory")
        key = secrets.token_bytes(32)
        wrapped = protect_synthetic_key(key)
        with (staging / "key.dpapi").open("xb") as out:
            out.write(wrapped)
        database = staging / "vault.db"
        with closing(_open_database(database, key, create=True)) as conn:
            mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            if not mode or mode[0].lower() != "wal":
                raise SyntheticVaultError("Synthetic WAL initialization failed")
            conn.execute("CREATE TABLE vault_meta(name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("INSERT INTO vault_meta(name,value) VALUES ('format','jarvis-synthetic-vault-v1')")
            conn.execute("CREATE TABLE fictional_records(id TEXT PRIMARY KEY, content TEXT NOT NULL)")
            conn.commit()
        with database.open("rb") as source:
            if source.read(16) == b"SQLite format 3\x00":
                raise SyntheticVaultError("Plaintext database detected; vault not created")
        if os.path.lexists(target):
            raise SyntheticVaultError("Synthetic vault target already exists")
        # This experiment is Windows-only: rename fails if the destination exists.
        os.rename(staging, target)
    return target


@contextmanager
def open_synthetic_vault(target: Path):
    """Unlock an existing synthetic vault; absent/corrupt keys never create a database."""
    require_synthetic_storage()
    _driver()
    target = Path(target)
    database, wrapped_file = target / "vault.db", target / "key.dpapi"
    if target.is_symlink() or getattr(target, "is_junction", lambda: False)() or database.is_symlink() or wrapped_file.is_symlink() or not target.is_dir() or not database.is_file() or not wrapped_file.is_file():
        raise SyntheticVaultError("Synthetic vault files are missing or unsupported")
    try:
        with wrapped_file.open("rb") as source:
            wrapped = source.read(16385)
        if not 1 <= len(wrapped) <= 16384:
            raise SyntheticVaultError("Invalid wrapped synthetic key")
        key = unprotect_synthetic_key(wrapped)
        conn = _open_database(database, key, create=False)
    except (OSError, ValueError, sqlite3.DatabaseError, cipher.DatabaseError) as exc:
        raise SyntheticVaultError("Synthetic vault could not be unlocked") from exc
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
