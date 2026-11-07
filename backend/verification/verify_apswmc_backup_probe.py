"""Test encrypted-to-encrypted backup mechanics only; not a user backup feature."""
import secrets
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
candidate = ROOT / ".jarvis-python314-apswmc-probe"
if sys.version_info[:2] != (3, 14) or not candidate.is_dir():
    print("APSWMC_BACKUP=NOT_RUN (isolated CPython 3.14 candidate missing)")
    raise SystemExit(2)
sys.path.insert(0, str(candidate))
try:
    import apsw
except (ImportError, OSError):
    print("APSWMC_BACKUP=NOT_RUN (candidate unavailable)")
    raise SystemExit(2)
if "Multiple Ciphers" not in getattr(apsw, "mc_version", ""):
    raise RuntimeError("Expected isolated encrypted driver")


def open_db(path, key, *, create=False):
    flags = apsw.SQLITE_OPEN_READWRITE | (apsw.SQLITE_OPEN_CREATE if create else 0)
    conn = apsw.Connection(str(path), flags=flags)
    try:
        if conn.pragma("cipher", "chacha20") != "chacha20" or conn.pragma("key", "raw:" + key.hex()) != "ok":
            raise RuntimeError("Cipher configuration failed")
        list(conn.execute("SELECT name FROM sqlite_master"))
        return conn
    except Exception:
        conn.close()
        raise


with tempfile.TemporaryDirectory(prefix="jarvis-apswmc-backup-") as directory:
    root = Path(directory)
    source, target, abandoned = root / "source.db", root / "copy.db", root / "abandoned.db"
    source_key, target_key, abandoned_key = secrets.token_bytes(32), secrets.token_bytes(32), secrets.token_bytes(32)
    marker = "FICTIONAL-BACKUP-" + secrets.token_hex(20)
    with closing(open_db(source, source_key, create=True)) as src:
        assert str(src.pragma("journal_mode", "WAL")).lower() == "wal"
        with src:
            src.execute("CREATE TABLE records(value TEXT NOT NULL)")
            src.execute("INSERT INTO records(value) VALUES (?)", (marker,))
            for _ in range(80):
                src.execute("INSERT INTO records(value) VALUES (?)", ("fictional filler " * 500,))
        assert Path(str(source) + "-wal").exists()
        with closing(open_db(abandoned, abandoned_key, create=True)) as dest:
            dest.execute("CREATE TABLE prior(value TEXT)")
            dest.execute("INSERT INTO prior VALUES ('preserved')")
            with dest.backup("main", src, "main") as backup:
                assert backup.step(1) is False
            assert dest.execute("SELECT value FROM prior").fetchone() == ("preserved",)
        with closing(open_db(target, target_key, create=True)) as dest:
            with dest.backup("main", src, "main") as backup:
                while not backup.step(16):
                    pass
            assert dest.execute("SELECT value FROM records ORDER BY rowid LIMIT 1").fetchone() == (marker,)
    with closing(open_db(target, target_key)) as restored:
        assert restored.execute("SELECT value FROM records ORDER BY rowid LIMIT 1").fetchone() == (marker,)
        assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    try:
        conn = open_db(target, source_key)
    except apsw.Error:
        pass
    else:
        conn.close()
        raise AssertionError("Target opened with source key")
    with closing(sqlite3.connect(target)) as plain:
        try:
            plain.execute("SELECT * FROM sqlite_master").fetchall()
        except sqlite3.DatabaseError:
            pass
        else:
            raise AssertionError("Plain SQLite opened backup")
    assert marker.encode() not in source.read_bytes() and marker.encode() not in target.read_bytes()
    print("DIFFERENT_KEYS_BACKUP_AND_REOPEN=PASS")
    print("ABORTED_BACKUP_PRESERVES_DESTINATION=PASS")
    print("PLAIN_SQLITE_AND_SOURCE_KEY_REJECTED=PASS")
print("APSWMC_BACKUP_PROBE=PASS")
print("PORTABLE_KEY_RECOVERY_AND_PRODUCTION_RESTORE=NOT_TESTED")
