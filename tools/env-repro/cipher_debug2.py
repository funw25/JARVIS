import hashlib
import secrets
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from sqlcipher3 import dbapi2 as cipher


def digest(path):
    data = Path(path).read_bytes()
    return len(data), hashlib.sha256(data).hexdigest()[:16], data[:16].hex()


directory = tempfile.mkdtemp(prefix="jarvis-cipher-debug2-")
target = Path(directory) / "fixture.db"
key, wrong = secrets.token_hex(32), secrets.token_hex(32)


def connect_with(k):
    connection = cipher.connect(str(target))
    connection.execute('PRAGMA key = "x%s"' % k)
    return connection


with closing(connect_with(key)) as c:
    print("cipher_version:", c.execute("PRAGMA cipher_version").fetchone())
    c.execute("PRAGMA temp_store=MEMORY")
    print("journal:", c.execute("PRAGMA journal_mode=WAL").fetchone())
    c.execute("CREATE TABLE fictional_content (value TEXT NOT NULL)")
    c.execute("INSERT INTO fictional_content VALUES ('MARKER')")
    c.commit()
    wal = Path(str(target) + "-wal")
    print("in-open wal:", wal.exists(), wal.stat().st_size if wal.exists() else 0)
    print("in-open main:", digest(target))

print("after close main:", digest(target))
print("after close wal exists:", Path(str(target) + "-wal").exists())

print("--- plain sqlite3 ---")
try:
    with closing(sqlite3.connect(target)) as plain:
        print("plain rows:", plain.execute("SELECT count(*) FROM sqlite_master").fetchone())
except Exception as exc:
    print("plain error:", type(exc).__name__, exc)
print("after plain main:", digest(target))

print("--- wrong key ---")
with closing(connect_with(wrong)) as bad:
    for sql in ("SELECT count(*) FROM sqlite_master", "SELECT name FROM sqlite_master"):
        try:
            print(sql, "->", bad.execute(sql).fetchall())
        except Exception as exc:
            print(sql, "-> error:", type(exc).__name__, exc)
    try:
        print("content ->", bad.execute("SELECT value FROM fictional_content").fetchall())
    except Exception as exc:
        print("content -> error:", type(exc).__name__, exc)
print("after wrong main:", digest(target))

print("--- correct key ---")
with closing(connect_with(key)) as good:
    print("content:", good.execute("SELECT value FROM fictional_content").fetchall())
print("after correct main:", digest(target))
