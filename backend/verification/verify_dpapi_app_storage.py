"""Exercise the app's experimental DPAPI-wrapped encrypted vault with fictional data."""
import os
import json
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".jarvis-python314-apswmc-probe"
sys.path[:0] = [str(ROOT / "backend"), str(PROBE)]
if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not PROBE.is_dir():
    print("DPAPI_APP_STORAGE=NOT_RUN (isolated Windows CPython 3.14 required)")
    raise SystemExit(2)
try:
    import apsw  # noqa: F401
except (ImportError, OSError):
    print("DPAPI_APP_STORAGE=NOT_RUN (candidate unavailable)")
    raise SystemExit(2)

from app import database
from app.security.storage_policy import StorageSecurityError
from experimental import vault_keys
from experimental.windows_dpapi import protect_synthetic_key
from usecases import m1_pipeline

names = ("JARVIS_DATA_MODE", "JARVIS_DATA_DIR", "JARVIS_VAULT_DIR", "JARVIS_STORAGE_ADAPTER", "JARVIS_APSWMC_KEY")
previous = {name: os.environ.get(name) for name in names}
try:
    with tempfile.TemporaryDirectory(prefix="jarvis-dpapi-app-") as directory:
        base = Path(directory)
        os.environ["JARVIS_DATA_MODE"] = "synthetic"
        os.environ["JARVIS_DATA_DIR"] = str(base / "unused-plain")
        os.environ.pop("JARVIS_APSWMC_KEY", None)
        vault = base / "vault"
        vault_keys.create_vault(vault)
        os.environ["JARVIS_VAULT_DIR"] = str(vault)
        os.environ["JARVIS_STORAGE_ADAPTER"] = "dpapi-vault"

        database.apply_migrations()
        row = m1_pipeline.load_dataset(ROOT / "backend" / "evals" / "m1" / "artifacts.jsonl")[4]
        with database.connect() as conn:
            transcript = m1_pipeline.run_item(conn, row)
            assert transcript["created"]["todo_id"] and transcript["created"]["draft_id"]
            assert m1_pipeline.materialize(conn, transcript["decision"]["decision_id"]) == transcript["created"]
        with database.connect() as conn:
            assert conn.execute("SELECT count(*) FROM m1_materializations").fetchone()[0] == 1
        print("DPAPI_APP_MIGRATIONS_AND_IDEMPOTENT_WORKFLOW=PASS")

        with database.connect() as conn:
            concurrent = m1_pipeline.decide(conn, transcript["suggestion"]["suggestion_id"], "accept")
        barrier = threading.Barrier(2)
        def run_concurrent(_):
            with database.connect() as conn:
                barrier.wait(timeout=10)
                return m1_pipeline.materialize(conn, concurrent["decision_id"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run_concurrent, range(2)))
        assert results[0] == results[1] and results[0]["todo_id"] and results[0]["draft_id"]
        print("DPAPI_APP_CONCURRENT_DECISION_MATERIALIZES_ONCE=PASS")

        with database.connect() as conn:
            multiprocess_decision = m1_pipeline.decide(conn, transcript["suggestion"]["suggestion_id"], "accept")
        worker = (
            "import json,sys; sys.path.insert(0, %r); "
            "from app import database; from usecases import m1_pipeline; "
            "sys.stdin.readline(); "
            "cm=database.connect(); conn=cm.__enter__(); "
            "result=m1_pipeline.materialize(conn, %r); "
            "cm.__exit__(None,None,None); print(json.dumps(result))"
        ) % (str(ROOT / "backend"), multiprocess_decision["decision_id"])
        workers = [subprocess.Popen([sys.executable, "-c", worker], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    encoding="utf-8", errors="replace",
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) for _ in range(2)]
        try:
            for process in workers:
                process.stdin.write("go\n")
                process.stdin.flush()
            outputs = [process.communicate(timeout=30) for process in workers]
            assert all(process.returncode == 0 for process in workers), outputs
            multiprocess_results = [json.loads(output[0].strip().splitlines()[-1]) for output in outputs]
            assert multiprocess_results[0] == multiprocess_results[1]
            assert multiprocess_results[0]["todo_id"] and multiprocess_results[0]["draft_id"]
        finally:
            for process in workers:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        print("DPAPI_APP_MULTIPROCESS_DECISION_MATERIALIZES_ONCE=PASS")

        db_path = vault / "vault.db"
        marker = "SYNTHETIC_PRIVATE_MARKER_20261114"
        with database.connect() as conn:
            conn.execute("CREATE TABLE protection_probe(value TEXT NOT NULL)")
            conn.execute("INSERT INTO protection_probe VALUES (?)", (marker,))
            conn.commit()
            observed = []
            for target in (db_path, Path(str(db_path) + "-wal"), Path(str(db_path) + "-shm")):
                if target.is_file():
                    observed.append(target.name)
                    assert marker.encode() not in target.read_bytes(), target.name
            assert db_path.name in observed
        print("DPAPI_APP_DB_AND_PRESENT_JOURNALS_HIDE_SYNTHETIC_MARKER=PASS files=%s" % ",".join(observed))
        assert db_path.read_bytes()[:16] != b"SQLite format 3\x00"
        with closing(sqlite3.connect(db_path)) as plain:
            try:
                plain.execute("SELECT name FROM sqlite_master").fetchall()
            except sqlite3.DatabaseError:
                pass
            else:
                raise AssertionError("plaintext sqlite3 opened the encrypted database")
        assert not (base / "unused-plain").exists()
        print("DPAPI_APP_ENCRYPTED_AND_NO_PLAINTEXT_FALLBACK=PASS")

        for adapter in ("", "sqlite3", "misspelled-adapter"):
            os.environ["JARVIS_STORAGE_ADAPTER"] = adapter
            try:
                database.connect()
            except database.StorageAdapterError:
                pass
            else:
                raise AssertionError("ambiguous adapter accepted: " + adapter)
        os.environ["JARVIS_STORAGE_ADAPTER"] = "dpapi-vault"
        os.environ["JARVIS_APSWMC_KEY"] = "00" * 32
        try:
            database.connect()
            raise AssertionError("environment key accepted alongside DPAPI vault")
        except database.StorageAdapterError:
            pass
        os.environ.pop("JARVIS_APSWMC_KEY")
        try:
            database.connect(base / "unexpected.sqlite3")
            raise AssertionError("conflicting path accepted")
        except database.StorageAdapterError:
            pass
        try:
            with database.connect_sqlite(base / "forced-plain.sqlite3"):
                raise AssertionError("direct plaintext connection bypassed vault configuration")
        except database.StorageAdapterError:
            pass
        assert not (base / "unexpected.sqlite3").exists()
        assert not (base / "forced-plain.sqlite3").exists()
        print("DPAPI_APP_CONFIGURATION_CONFLICTS_FAIL_CLOSED=PASS")

        wrong = base / "wrong-key"
        vault_keys.create_vault(wrong)
        (wrong / vault_keys.KEYFILE).write_bytes(protect_synthetic_key(secrets.token_bytes(32)))
        os.environ["JARVIS_VAULT_DIR"] = str(wrong)
        try:
            with database.connect():
                raise AssertionError("wrong key opened vault")
        except vault_keys.VaultError:
            pass
        assert not (base / "unused-plain").exists()
        os.environ["JARVIS_DATA_MODE"] = "personal"
        try:
            database.connect()
            raise AssertionError("personal data mode unexpectedly enabled")
        except StorageSecurityError:
            pass
        print("DPAPI_APP_WRONG_KEY_AND_PERSONAL_MODE_FAIL_CLOSED=PASS")
finally:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

print("DPAPI_APP_STORAGE=PASS (fictional data only; G1 and real-data approval remain open)")
