"""Terminate only a synthetic child process to probe encrypted WAL recovery."""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
candidate = ROOT / ".jarvis-python314-apswmc-probe"
if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not candidate.is_dir():
    print("APSWMC_CRASH=NOT_RUN (isolated Windows CPython 3.14 candidate required)")
    raise SystemExit(2)
sys.path.insert(0, str(candidate))
sys.path.insert(0, str(ROOT / "backend"))
try:
    import apsw
except (ImportError, OSError):
    print("APSWMC_CRASH=NOT_RUN (candidate unavailable)")
    raise SystemExit(2)
from experimental import apswmc_synthetic_vault as vault

MARKER = "FICTIONAL_UNCOMMITTED_CRASH_PROBE"


if len(sys.argv) == 3 and sys.argv[1] == "--crash-child":
    with vault.open_vault(Path(sys.argv[2])) as conn:
        conn.pragma("cache_size", 10)
        for i in range(400):
            conn.execute("INSERT INTO fictional_records(id,content) VALUES (?,?)", ("pending-" + str(i), (MARKER + "-") * 200))
        print("READY", flush=True)
        sys.stdin.read(1)
    raise SystemExit(0)


with tempfile.TemporaryDirectory(prefix="jarvis-apswmc-crash-", ignore_cleanup_errors=True) as directory:
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    target = Path(directory) / "vault"
    vault.initialize(target)
    with vault.open_vault(target) as conn:
        conn.execute("INSERT INTO fictional_records(id,content) VALUES ('committed','fictional committed')")
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--crash-child", str(target)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=dict(os.environ))
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(process.stdout.readline)
            try:
                assert future.result(timeout=20).strip() == "READY", "Child did not reach uncommitted state"
                wal = Path(str(target / "vault.db") + "-wal")
                assert wal.is_file() and wal.stat().st_size > 0
                assert MARKER.encode() not in wal.read_bytes()
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream:
                stream.close()
    with vault.open_vault(target) as conn:
        assert conn.execute("SELECT content FROM fictional_records WHERE id='committed'").fetchone() == ("fictional committed",)
        assert conn.execute("SELECT COUNT(*) FROM fictional_records WHERE id LIKE 'pending-%'").fetchone() == (0,)
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    with closing(sqlite3.connect(target / "vault.db")) as plain:
        try:
            plain.execute("SELECT * FROM sqlite_master").fetchall()
        except sqlite3.DatabaseError:
            pass
        else:
            raise AssertionError("Plain SQLite opened the crash-test database")
    assert MARKER.encode() not in (target / "vault.db").read_bytes()
    print("PROCESS_TERMINATION_RECOVERS_COMMITTED_AND_ROLLS_BACK_PENDING=PASS")
    print("NONEMPTY_WAL_MARKER_AND_PLAINTEXT_REJECTION=PASS")
    leftover = Path(directory)
    for _ in range(30):
        if not leftover.exists():
            break
        time.sleep(0.1)
        shutil.rmtree(leftover, ignore_errors=True)
    if leftover.exists():
        print("TEMP_CLEANUP_RESIDUE=" + str(leftover))
print("APSWMC_SYNTHETIC_CRASH_PROBE=PASS")
print("POWER_LOSS_AND_PRODUCTION_RECOVERY=NOT_TESTED")
