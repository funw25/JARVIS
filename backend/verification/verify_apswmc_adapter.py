"""Run the published storage contract against the experimental APSW SQLite3MC adapter.

Synthetic temporary data only. Proves the adapter can satisfy the behaviour the
services rely on while the database is actually encrypted; it does not integrate
the adapter into the application, and it is not a security review.
"""
import os
import secrets
import sqlite3
from contextlib import closing
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".jarvis-python314-apswmc-probe"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(PROBE))

if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not PROBE.is_dir():
    print("APSWMC_ADAPTER=NOT_RUN (isolated Windows CPython 3.14 candidate required)")
    raise SystemExit(2)
try:
    import apsw
except (ImportError, OSError):
    print("APSWMC_ADAPTER=NOT_RUN (candidate unavailable)")
    raise SystemExit(2)

from app.security.storage_policy import StorageSecurityError
from experimental import apswmc_storage as storage


def raw_open(path, key, busy_ms=0):
    connection = apsw.Connection(str(path))
    connection.pragma("cipher", "chacha20")
    connection.pragma("key", "raw:" + key.hex())
    connection.pragma("busy_timeout", busy_ms)
    list(connection.execute("SELECT name FROM sqlite_master"))
    return connection


with tempfile.TemporaryDirectory(prefix="jarvis-apswmc-adapter-") as directory:
    base = Path(directory)
    target = base / "adapter.db"
    key = secrets.token_bytes(32)
    wrong = secrets.token_bytes(32)

    # 1. The storage gate must fail closed and must not create the database file.
    os.environ.pop("JARVIS_DATA_MODE", None)
    try:
        with storage.connect(target, key):
            raise AssertionError("storage gate accepted an unconfigured mode")
    except StorageSecurityError:
        pass
    assert not target.exists(), "storage gate created an encrypted database"
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    print("ADAPTER_GATE_REFUSES_WITHOUT_CREATING_FILES=PASS")

    # 2. Create once, then open/close repeatedly with an intact database.
    with storage.connect(target, key) as connection:
        connection.execute("CREATE TABLE tx_probe(id INTEGER PRIMARY KEY, name TEXT, note TEXT)")
        connection.execute("INSERT INTO tx_probe VALUES (1,'v1',NULL)")
    for _ in range(3):
        with storage.connect(target, key, create=False) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    print("ADAPTER_OPEN_CLOSE_REPEATEDLY_AND_INTEGRITY=PASS")

    # 3. Uncommitted work stays invisible to another connection until commit.
    with storage.connect(target, key, create=False) as connection:
        connection.execute("UPDATE tx_probe SET name='pending' WHERE id=1")
        outside = raw_open(target, key)
        try:
            assert outside.execute("SELECT name FROM tx_probe WHERE id=1").fetchone()[0] == "v1"
        finally:
            outside.close()
    outside = raw_open(target, key)
    try:
        assert outside.execute("SELECT name FROM tx_probe WHERE id=1").fetchone()[0] == "pending"
    finally:
        outside.close()
    print("ADAPTER_UNCOMMITTED_CHANGES_INVISIBLE_UNTIL_COMMIT=PASS")

    # 4. The adapter's first write takes BEGIN IMMEDIATE, blocking a zero-timeout writer.
    with storage.connect(target, key, create=False) as connection:
        connection.execute("UPDATE tx_probe SET name='held' WHERE id=1")
        assert connection.in_transaction
        contender = raw_open(target, key, busy_ms=0)
        try:
            try:
                contender.execute("BEGIN IMMEDIATE")
                raise AssertionError("second writer acquired the immediate lock")
            except apsw.Error as exc:
                assert "lock" in str(exc).lower() or "busy" in str(exc).lower(), str(exc)
        finally:
            contender.close()
    print("ADAPTER_IMMEDIATE_WRITE_LOCK=PASS")

    # 5. busy_timeout waits for a held writer instead of failing at once.
    holder = raw_open(target, key)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("UPDATE tx_probe SET name='waited' WHERE id=1")
    waiter = raw_open(target, key, busy_ms=400)
    try:
        started = __import__("time").monotonic()
        try:
            waiter.execute("BEGIN IMMEDIATE")
            raise AssertionError("waiter acquired a held write lock")
        except apsw.Error:
            pass
        elapsed = __import__("time").monotonic() - started
    finally:
        waiter.close()
        holder.execute("ROLLBACK")
        holder.close()
    assert elapsed >= 0.25, "busy_timeout did not wait: %.3f" % elapsed
    print("ADAPTER_BUSY_TIMEOUT_WAITS_FOR_WRITER=PASS")

    # 6. A stale writer is detected through rowcount instead of overwriting.
    with storage.connect(target, key, create=False) as connection:
        connection.execute("UPDATE tx_probe SET name='v1', note='first' WHERE id=1")
    reader = raw_open(target, key)
    try:
        stale = reader.execute("SELECT name FROM tx_probe WHERE id=1").fetchone()[0]
    finally:
        reader.close()
    with storage.connect(target, key, create=False) as connection:
        changed = connection.execute(
            "UPDATE tx_probe SET name='a', note='by a' WHERE id=1 AND name=?", (stale,)
        ).rowcount
    assert changed == 1
    with storage.connect(target, key, create=False) as connection:
        lost = connection.execute(
            "UPDATE tx_probe SET name='b', note='by b' WHERE id=1 AND name=?", (stale,)
        ).rowcount
    assert lost == 0, "stale writer silently overwrote a newer row"
    with storage.connect(target, key, create=False) as connection:
        row = connection.execute("SELECT name, note FROM tx_probe WHERE id=1").fetchone()
        assert (row["name"], row["note"]) == ("a", "by a")
    print("ADAPTER_LOST_UPDATE_DETECTED_BY_ROWCOUNT=PASS")

    # 7. Row shape: name/position access, keys, dict, NULL vs empty, Unicode, rowcount, cursor.
    marker = "SYNTHETIC-ADAPTER-MARKER-" + secrets.token_hex(8)
    with storage.connect(target, key, create=False) as connection:
        connection.execute("INSERT INTO tx_probe VALUES (2,?,?)", ("中文与 emoji 🚀", ""))
        connection.execute("INSERT INTO tx_probe VALUES (3,?,NULL)", (marker,))
        row = connection.execute("SELECT id, name, note FROM tx_probe WHERE id=2").fetchone()
        assert row[0] == row["id"] == 2
        assert row["name"] == "中文与 emoji 🚀" and row["note"] == ""
        assert row.keys() == ["id", "name", "note"]
        assert dict(row) == {"id": 2, "name": "中文与 emoji 🚀", "note": ""}
        null_row = connection.execute("SELECT note FROM tx_probe WHERE id=3").fetchone()
        assert null_row[0] is None
        try:
            null_row["missing"]
            raise AssertionError("missing column did not raise IndexError")
        except IndexError:
            pass
        assert connection.execute("DELETE FROM tx_probe WHERE id=99").rowcount == 0
        assert connection.execute("DELETE FROM tx_probe WHERE id=3").rowcount == 1
        cursor = connection.execute("SELECT id FROM tx_probe ORDER BY id")
        assert [item[0] for item in cursor] == [1, 2]
        assert len(connection.execute("SELECT id FROM tx_probe ORDER BY id").fetchmany(1)) == 1
        assert len(connection.execute("SELECT id FROM tx_probe ORDER BY id").fetchall()) == 2
    print("ADAPTER_ROW_SHAPE_KEYS_DICT_NULL_UNICODE_AND_ROWCOUNT=PASS")

    # 8. A closed adapter connection must refuse further use.
    with storage.connect(target, key, create=False) as connection:
        closed = connection
    try:
        closed.execute("SELECT 1")
        raise AssertionError("closed adapter connection still accepted statements")
    except (apsw.Error, RuntimeError):
        pass
    print("ADAPTER_USE_AFTER_CLOSE_REJECTED=PASS")

    # 9. The database must really be encrypted and the wrong key must be rejected.
    assert not storage.plaintext_header(target), "database has a plaintext SQLite header"
    with open(target, "rb") as source:
        assert marker.encode() not in source.read(), "test marker found in the database file"
    probe = raw_open(target, key)
    try:
        assert "Multiple Ciphers" in apsw.mc_version, apsw.mc_version
        assert probe.pragma("cipher") == "chacha20"
    finally:
        probe.close()
    with closing(sqlite3.connect(target)) as plain, plain:
        try:
            plain.execute("SELECT count(*) FROM sqlite_master").fetchone()
            raise AssertionError("plain sqlite3 opened the encrypted database")
        except sqlite3.DatabaseError:
            pass
    try:
        raw_open(target, wrong).close()
        raise AssertionError("wrong key opened the encrypted database")
    except apsw.Error:
        pass
    print("ADAPTER_CIPHERTEXT_WRONG_KEY_AND_PLAINTEXT_REJECTION=PASS")

print("APSWMC_ADAPTER_CONTRACT=PASS")
print("APPLICATION_INTEGRATION_BACKUP_RECOVERY_AND_SECURITY_REVIEW=NOT_TESTED")
