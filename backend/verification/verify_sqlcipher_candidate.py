"""Probe a locally installed SQLCipher candidate on temporary synthetic data only.

This does not integrate encryption into Jarvis, test key recovery or prove safety.
The package must already be installed in the interpreter running this script.
"""
import importlib.util
import re
import secrets
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

if importlib.util.find_spec("sqlcipher3") is None:
    print("SQLCIPHER_CANDIDATE_VERIFICATION=NOT_RUN (sqlcipher3 is not installed)")
    raise SystemExit(2)
from sqlcipher3 import dbapi2 as cipher


def keyed_connection(path, key):
    if not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None:
        raise ValueError("Probe key must contain exactly 64 lowercase hexadecimal characters")
    connection = cipher.connect(str(path))
    try:
        # Raw 256-bit test key; never log this statement or use real user keys.
        connection.execute(f"""PRAGMA key = "x'{key}'" """)
    except Exception:
        connection.close()
        raise
    return connection


def assert_denied(connection, label):
    try:
        connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except Exception as exc:
        if isinstance(exc, (cipher.DatabaseError, sqlite3.DatabaseError)):
            return
        raise
    raise AssertionError(label + " unexpectedly read the encrypted schema")


with tempfile.TemporaryDirectory(prefix="jarvis-cipher-probe-") as directory:
    target = Path(directory) / "fixture.db"
    marker = b"JARVIS_ONLY_FICTIONAL_SQLCIPHER_MARKER_2026"
    key, wrong = secrets.token_hex(32), secrets.token_hex(32)
    with closing(keyed_connection(target, key)) as connection:
        version = connection.execute("PRAGMA cipher_version").fetchone()
        if not version or not version[0]:
            raise RuntimeError("SQLCipher version unavailable; encryption cannot be assumed")
        connection.execute("PRAGMA temp_store=MEMORY")
        mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
        if not mode or mode[0].lower() != "wal":
            raise RuntimeError("Could not enable WAL for the probe")
        connection.execute("CREATE TABLE fictional_content (value TEXT NOT NULL)")
        connection.execute("INSERT INTO fictional_content VALUES (?)", (marker.decode("ascii"),))
        connection.commit()
        wal = Path(str(target) + "-wal")
        if not wal.exists() or wal.stat().st_size == 0:
            raise AssertionError("WAL was not created for the probe")
        if target.read_bytes().startswith(b"SQLite format 3\x00"):
            raise AssertionError("Database has a plaintext SQLite header")
        if marker in target.read_bytes() or marker in wal.read_bytes():
            raise AssertionError("Test marker found in database or WAL")
    with closing(sqlite3.connect(target)) as plain:
        assert_denied(plain, "Standard SQLite")
    with closing(keyed_connection(target, wrong)) as bad:
        assert_denied(bad, "Wrong key")
    with closing(keyed_connection(target, key)) as reopened:
        value = reopened.execute("SELECT value FROM fictional_content").fetchone()
        if not value or value[0].encode("ascii") != marker:
            raise AssertionError("Correct key did not recover the marker")
print("CANDIDATE_CORRECT_KEY_WRONG_KEY_PLAINTEXT_SQLITE_AND_WAL=PASS")
print("KEY_RECOVERY_BACKUP_TEMP_FILES_MIGRATION_AND_SECURITY_REVIEW=NOT_TESTED")
