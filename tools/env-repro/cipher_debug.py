import secrets
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from sqlcipher3 import dbapi2 as cipher

directory = tempfile.mkdtemp(prefix="jarvis-cipher-debug-")
target = Path(directory) / "fixture.db"
key = secrets.token_hex(32)
wrong = secrets.token_hex(32)
print("dir:", directory)
print("key:", key[:12], "...  wrong:", wrong[:12], "...")


def connect_with(path, k):
    connection = cipher.connect(str(path))
    connection.execute('PRAGMA key = "x%s"' % k)
    return connection


with closing(connect_with(target, key)) as connection:
    print("cipher_version:", connection.execute("PRAGMA cipher_version").fetchone())
    print("cipher_provider:", connection.execute("PRAGMA cipher_provider").fetchone())
    print("journal_mode:", connection.execute("PRAGMA journal_mode=WAL").fetchone())
    connection.execute("CREATE TABLE t (v TEXT NOT NULL)")
    connection.execute("INSERT INTO t VALUES (?)", ("MARKER_VALUE",))
    connection.commit()

raw = target.read_bytes()
print("main size:", len(raw), "header:", raw[:16].hex(), "has_marker:", b"MARKER_VALUE" in raw)
wal = Path(str(target) + "-wal")
print("wal exists:", wal.exists(), "wal size:", wal.stat().st_size if wal.exists() else 0)

print("--- plain sqlite3 ---")
try:
    with closing(sqlite3.connect(target)) as plain:
        print("plain rows:", plain.execute("SELECT count(*) FROM sqlite_master").fetchone())
except Exception as exc:
    print("plain error:", type(exc).__name__, exc)

print("--- wrong key ---")
with closing(connect_with(target, wrong)) as bad:
    for sql in ("PRAGMA cipher_integrity_check", "SELECT count(*) FROM sqlite_master"):
        try:
            print(sql, "->", bad.execute(sql).fetchall())
        except Exception as exc:
            print(sql, "-> error:", type(exc).__name__, exc)
    try:
        print("tables:", bad.execute("SELECT name FROM sqlite_master").fetchall())
        print("read t:", bad.execute("SELECT v FROM t").fetchall())
    except Exception as exc:
        print("read t -> error:", type(exc).__name__, exc)

print("--- correct key ---")
with closing(connect_with(target, key)) as good:
    print("read t:", good.execute("SELECT v FROM t").fetchall())

print("--- wrong key on a fresh connection, before any schema read ---")
with closing(connect_with(target, wrong)) as bad2:
    try:
        print("bad2 t:", bad2.execute("SELECT v FROM t").fetchall())
    except Exception as exc:
        print("bad2 error:", type(exc).__name__, exc)
