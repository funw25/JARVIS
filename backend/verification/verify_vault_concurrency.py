"""Verify S4 concurrency: no lost updates, no deadlock, clean temporary files.

Synthetic temporary vault only; writers and readers run against the experimental
encrypted backend with the adapter's WAL and busy_timeout settings.
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".jarvis-python314-apswmc-probe"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(PROBE))

if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not PROBE.is_dir():
    print("VAULT_CONCURRENCY=NOT_RUN (isolated Windows CPython 3.14 required)")
    raise SystemExit(2)
try:
    import apsw  # noqa: F401
except (ImportError, OSError):
    print("VAULT_CONCURRENCY=NOT_RUN (candidate unavailable)")
    raise SystemExit(2)

os.environ["JARVIS_DATA_MODE"] = "synthetic"

from experimental import vault_keys as keys

WRITERS = 4
INCREMENTS = 40
READERS = 3
EXPECTED = WRITERS * INCREMENTS


with tempfile.TemporaryDirectory(prefix="jarvis-vault-concurrency-") as directory:
    vault = Path(directory) / "vault"
    keys.create_vault(vault)
    with keys.open_vault(vault) as connection:
        connection.execute("CREATE TABLE counter(id INTEGER PRIMARY KEY CHECK (id = 1), value INTEGER NOT NULL)")
        connection.execute("INSERT INTO counter VALUES (1, 0)")

    stop = threading.Event()
    failures = []
    samples = []
    writer_done = [0] * WRITERS

    def writer(index):
        try:
            with keys.open_vault(vault) as connection:
                for _ in range(INCREMENTS):
                    connection.begin_immediate()
                    connection.execute("UPDATE counter SET value = value + 1 WHERE id = 1")
                    connection.commit()
                    time.sleep(0.002)
                writer_done[index] = 1
        except Exception as exc:  # noqa: BLE001 - any failure is a finding
            failures.append(("writer", type(exc).__name__, str(exc)))

    def reader(index):
        try:
            with keys.open_vault(vault) as connection:
                last = -1
                while not stop.is_set():
                    value = connection.execute("SELECT value FROM counter WHERE id = 1").fetchone()[0]
                    if value < last:
                        failures.append(("reader", "REGression", "%d -> %d" % (last, value)))
                        return
                    last = value
                    samples.append(value)
                    time.sleep(0.002)
        except Exception as exc:  # noqa: BLE001
            failures.append(("reader", type(exc).__name__, str(exc)))

    writers = [threading.Thread(target=writer, args=(i,)) for i in range(WRITERS)]
    readers = [threading.Thread(target=reader, args=(i,)) for i in range(READERS)]
    started = time.monotonic()
    for thread in writers + readers:
        thread.start()
    for thread in writers:
        thread.join(timeout=120)
    stop.set()
    for thread in readers:
        thread.join(timeout=30)
    elapsed = time.monotonic() - started

    assert not failures, failures
    assert all(thread.is_alive() is False for thread in writers + readers), "a worker did not finish (deadlock?)"
    assert all(writer_done), "a writer did not complete all increments"
    print("S4_NO_DEADLOCK_AND_ALL_WORKERS_FINISHED=PASS %.2fs" % elapsed)

    with keys.open_vault(vault) as connection:
        total = connection.execute("SELECT value FROM counter WHERE id = 1").fetchone()[0]
    assert total == EXPECTED, "lost updates: %d of %d" % (total, EXPECTED)
    print("S4_NO_LOST_UPDATES=PASS %d/%d" % (total, EXPECTED))

    assert samples, "readers never observed the counter"
    assert max(samples) <= EXPECTED
    print("S4_READERS_SEE_MONOTONIC_VALUES=PASS samples=%d max=%d" % (len(samples), max(samples)))

    # After every connection is closed the WAL side files should disappear.
    for _ in range(40):
        if not (vault / (keys.DBFILE + "-wal")).exists():
            break
        time.sleep(0.05)
    leftovers = [name for name in (keys.DBFILE + "-wal", keys.DBFILE + "-shm") if (vault / name).exists()]
    assert not leftovers, leftovers
    print("S4_NO_TEMPORARY_FILES_LEFT=PASS")

print("VAULT_CONCURRENCY=PASS")
print("CONNECTION_POOL_TUNING=NOT_IMPLEMENTED")
