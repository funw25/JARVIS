"""Storage transaction contract for the current sqlite3 implementation.

Covers the behaviours every storage adapter must keep, using synthetic data only:
gate refusal without side effects, open/close, explicit read/write transactions,
isolation, immediate write locks with timeout=0 and busy_timeout, lost-update
detection through rowcount, row-shape edges (missing column, NULL, Unicode),
and use-after-close. It does not test encryption or any APSW adapter.
"""
import os
import gc
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["JARVIS_DATA_MODE"] = "synthetic"

import app.database as database
from app.security.storage_policy import StorageSecurityError


def raw(path):
    connection = sqlite3.connect(path, timeout=0, isolation_level=None)
    connection.row_factory = sqlite3.Row
    return connection


with tempfile.TemporaryDirectory(prefix="jarvis-storage-tx-", ignore_cleanup_errors=True) as directory:
    data_dir = Path(directory) / "data"
    os.environ["JARVIS_DATA_DIR"] = str(data_dir)

    # 1. The storage gate must fail closed without creating files or directories.
    blocked_dir = Path(directory) / "blocked"
    os.environ["JARVIS_DATA_DIR"] = str(blocked_dir)
    os.environ.pop("JARVIS_DATA_MODE", None)
    try:
        with database.connect():
            raise AssertionError("storage gate accepted an unconfigured mode")
    except StorageSecurityError:
        pass
    assert not blocked_dir.exists(), "storage gate created a directory or file"
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    os.environ["JARVIS_DATA_DIR"] = str(data_dir)
    print("GATE_REFUSES_WITHOUT_CREATING_FILES=PASS")

    # 2. Initialise once, then open/close repeatedly with an intact database.
    database.apply_migrations()
    path = database.database_path()
    for _ in range(3):
        with database.connect() as conn:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with database.connect() as conn:
        conn.execute("CREATE TABLE tx_probe(id INTEGER PRIMARY KEY, name TEXT, note TEXT)")
        conn.execute("INSERT INTO tx_probe VALUES (1,'v1',NULL)")
    print("OPEN_CLOSE_REPEATEDLY_AND_INTEGRITY=PASS")

    # 3. Work inside one connection's transaction is invisible until commit.
    with database.connect() as conn:
        conn.execute("UPDATE tx_probe SET name='pending' WHERE id=1")
        with closing(raw(path)) as outside:
            assert outside.execute("SELECT name FROM tx_probe WHERE id=1").fetchone()[0] == "v1"
    with closing(raw(path)) as outside:
        assert outside.execute("SELECT name FROM tx_probe WHERE id=1").fetchone()[0] == "pending"
    print("UNCOMMITTED_CHANGES_INVISIBLE_UNTIL_COMMIT=PASS")

    # 4. BEGIN IMMEDIATE acquires the write lock; timeout=0 surfaces it as locked.
    first = raw(path)
    try:
        first.execute("BEGIN IMMEDIATE")
        with closing(raw(path)) as second:
            try:
                second.execute("BEGIN IMMEDIATE")
                raise AssertionError("second writer acquired the immediate lock")
            except sqlite3.OperationalError as exc:
                assert "lock" in str(exc).lower(), str(exc)
        first.execute("COMMIT")
        with closing(raw(path)) as third:
            third.execute("BEGIN IMMEDIATE")
            third.execute("ROLLBACK")
    finally:
        if first.in_transaction:
            first.execute("ROLLBACK")
        first.close()
    print("IMMEDIATE_WRITE_LOCK_AND_ZERO_TIMEOUT=PASS")

    # 5. busy_timeout waits for a held writer instead of failing immediately.
    # check_same_thread=False is required because the commit comes from a helper
    # thread; the connection is still only used by one thread at a time.
    holder = sqlite3.connect(path, timeout=0, isolation_level=None, check_same_thread=False)
    holder.row_factory = sqlite3.Row
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("UPDATE tx_probe SET name='held' WHERE id=1")
    timing = {}

    def release_later():
        time.sleep(0.4)
        holder.execute("COMMIT")

    worker = threading.Thread(target=release_later)
    worker.start()
    waiter = sqlite3.connect(path, timeout=0, isolation_level=None)
    try:
        waiter.execute("PRAGMA busy_timeout = 5000")
        start = time.monotonic()
        waiter.execute("BEGIN IMMEDIATE")
        timing["waited"] = time.monotonic() - start
        waiter.execute("ROLLBACK")
    finally:
        waiter.close()
        worker.join(timeout=10)
        if holder.in_transaction:
            holder.execute("ROLLBACK")
        holder.close()
    assert timing["waited"] >= 0.15, timing
    with closing(raw(path)) as check:
        assert check.execute("SELECT name FROM tx_probe WHERE id=1").fetchone()[0] == "held"
    print("BUSY_TIMEOUT_WAITS_FOR_WRITER=PASS")

    # 6. Concurrent writers must detect a lost update instead of overwriting it.
    with closing(raw(path)) as setup:
        setup.execute("UPDATE tx_probe SET name='v1', note='first' WHERE id=1")
    with closing(raw(path)) as writer_a, closing(raw(path)) as writer_b:
        seen_a = writer_a.execute("SELECT name FROM tx_probe WHERE id=1").fetchone()[0]
        seen_b = writer_b.execute("SELECT name FROM tx_probe WHERE id=1").fetchone()[0]
        assert seen_a == seen_b == "v1"
        writer_a.execute("BEGIN IMMEDIATE")
        changed = writer_a.execute("UPDATE tx_probe SET name='a', note='by a' WHERE id=1 AND name=?", (seen_a,)).rowcount
        writer_a.execute("COMMIT")
        assert changed == 1
        writer_b.execute("BEGIN IMMEDIATE")
        stale = writer_b.execute("UPDATE tx_probe SET name='b', note='by b' WHERE id=1 AND name=?", (seen_b,)).rowcount
        writer_b.execute("COMMIT")
        assert stale == 0, "stale writer silently overwrote a newer row"
    with closing(raw(path)) as check:
        row = check.execute("SELECT name, note FROM tx_probe WHERE id=1").fetchone()
        assert (row["name"], row["note"]) == ("a", "by a")
    print("LOST_UPDATE_DETECTED_BY_ROWCOUNT=PASS")

    # 7. Row-shape contract: missing column, NULL, Unicode, rowcount, iteration.
    with database.connect() as conn:
        conn.execute("INSERT INTO tx_probe VALUES (2,?,?)", ("中文与 emoji 🚀", ""))
        conn.execute("INSERT INTO tx_probe VALUES (3,'null-note',NULL)")
        null_row = conn.execute("SELECT note FROM tx_probe WHERE id=3").fetchone()
        assert null_row[0] is None
        text_row = conn.execute("SELECT name, note FROM tx_probe WHERE id=2").fetchone()
        assert text_row["name"] == "中文与 emoji 🚀" and text_row["note"] == ""
        # NULL and the empty string must stay distinguishable.
        assert conn.execute("SELECT count(*) FROM tx_probe WHERE note IS NULL").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM tx_probe WHERE note = ''").fetchone()[0] == 1
        try:
            null_row["missing_column"]
            raise AssertionError("missing column did not raise IndexError")
        except IndexError:
            pass
        assert conn.execute("DELETE FROM tx_probe WHERE id=99").rowcount == 0
        cursor = conn.execute("SELECT id FROM tx_probe ORDER BY id")
        assert [row[0] for row in cursor] == [1, 2, 3]
        cursor = conn.execute("SELECT id FROM tx_probe ORDER BY id")
        assert len(cursor.fetchmany(1)) == 1
    print("ROW_SHAPE_MISSING_COLUMN_NULL_UNICODE_AND_ROWCOUNT=PASS")

    # 8. A closed connection must refuse further use instead of silently reopening.
    with database.connect() as conn:
        closed = conn
    try:
        closed.execute("SELECT 1")
        raise AssertionError("closed connection still accepted statements")
    except sqlite3.ProgrammingError:
        pass
    print("USE_AFTER_CLOSE_REJECTED=PASS")

    # Windows keeps the database file handle until every connection is collected,
    # so release the remaining references before the temporary directory is removed.
    closed = first = holder = waiter = cursor = outside = None
    gc.collect()
    leftover = Path(directory)
    for _ in range(30):
        if not leftover.exists():
            break
        time.sleep(0.1)
        shutil.rmtree(leftover, ignore_errors=True)
    if leftover.exists():
        print("TEMP_CLEANUP_RESIDUE=" + str(leftover))

print("SQLITE_STORAGE_TRANSACTION_CONTRACT=PASS")
print("ENCRYPTION_AND_APSW_ADAPTER=NOT_TESTED")
