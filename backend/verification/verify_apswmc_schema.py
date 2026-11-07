"""Try existing Jarvis migration SQL on an isolated encrypted candidate only."""
import hashlib
import os
import secrets
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
candidate = ROOT / ".jarvis-python314-apswmc-probe"
if sys.version_info[:2] != (3, 14) or not candidate.is_dir():
    print("APSWMC_SCHEMA=NOT_RUN (isolated CPython 3.14 candidate missing)")
    raise SystemExit(2)
sys.path.insert(0, str(candidate))
sys.path.insert(0, str(ROOT / "backend"))
try:
    import apsw
except (ImportError, OSError):
    print("APSWMC_SCHEMA=NOT_RUN (candidate not loadable)")
    raise SystemExit(2)
from app.database import MIGRATIONS, _check_statement, _statements

if "Multiple Ciphers" not in getattr(apsw, "mc_version", ""):
    raise RuntimeError("Unexpected driver")


def open_db(path, key, create=False):
    flags = apsw.SQLITE_OPEN_READWRITE | (apsw.SQLITE_OPEN_CREATE if create else 0)
    conn = apsw.Connection(str(path), flags=flags)
    try:
        if conn.pragma("cipher", "chacha20") != "chacha20" or conn.pragma("key", "raw:" + key.hex()) != "ok":
            raise RuntimeError("Encryption configuration failed")
        list(conn.execute("SELECT name FROM sqlite_master"))
        conn.pragma("foreign_keys", 1)
        assert conn.pragma("foreign_keys") == 1
        return conn
    except Exception:
        conn.close()
        raise


with tempfile.TemporaryDirectory(prefix="jarvis-apswmc-schema-") as directory:
    path = Path(directory) / "schema.db"
    key = secrets.token_bytes(32)
    marker = "SYNTHETIC-SCHEMA-" + secrets.token_hex(20)
    files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    assert len(files) >= 14
    with closing(open_db(path, key, create=True)) as conn:
        assert str(conn.pragma("journal_mode", "WAL")).lower() == "wal"
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,name TEXT NOT NULL,applied_at TEXT NOT NULL,sha256 TEXT NOT NULL)")
            for file in files:
                raw = file.read_bytes()
                for statement in _statements(raw.decode("utf-8")):
                    _check_statement(statement)
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_migrations(version,name,applied_at,sha256) VALUES (?,?,?,?)",
                             (int(file.name[:4]), file.name, "fictional", hashlib.sha256(raw).hexdigest()))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        timestamp = "2027-01-01T00:00:00Z"
        conn.execute("INSERT INTO todos(id,owner_id,created_by_assistant,title,description,status,priority,due_at,created_at,updated_at) VALUES (?,?,?,?,?,'open','normal',?,?,?)",
                     ("task", "synthetic-owner", "jarvis", marker, "", timestamp, timestamp, timestamp))
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE todos SET title='rolled back' WHERE id='task'")
        conn.execute("ROLLBACK")
        assert list(conn.execute("SELECT title,schedule_revision FROM todos WHERE id='task'")) == [(marker, 1)]
        assert not list(conn.execute("PRAGMA foreign_key_check"))
        assert isinstance(conn.execute("SELECT title FROM todos").fetchone(), tuple)
        assert not hasattr(conn, "commit")
        wal = Path(str(path) + "-wal")
        assert wal.is_file() and marker.encode("ascii") not in wal.read_bytes()
    with closing(open_db(path, key)) as conn:
        assert list(conn.execute("SELECT title FROM todos")) == [(marker,)]
        assert list(conn.execute("SELECT COUNT(*) FROM schema_migrations")) == [(len(files),)]
    assert marker.encode("ascii") not in path.read_bytes()
    print("CURRENT_MIGRATION_SQL_AND_ROLLBACK_ON_CANDIDATE=PASS")
    print("APSW_API_DIFFERS_FROM_SQLITE3=CONFIRMED")
print("APSWMC_SYNTHETIC_SCHEMA=PASS")
