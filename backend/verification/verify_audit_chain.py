"""Synthetic audit chain contracts, including limits without an external anchor."""
import json
import os
import sys
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["JARVIS_DATA_MODE"] = "synthetic"
for name in ("JARVIS_STORAGE_ADAPTER", "JARVIS_APSWMC_KEY", "JARVIS_VAULT_DIR"):
    os.environ.pop(name, None)

from app import database
from usecases import audit_chain, m1_pipeline

assert audit_chain.canonical_bytes({"z": None, "a": "e\u0301"}) == audit_chain.canonical_bytes({"a": "é", "z": None})
for invalid in (1.5, 2**63, {"e\u0301": 1, "é": 2}, {"x": float("nan")}):
    try:
        audit_chain.canonical_bytes(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid canonical value accepted")

with tempfile.TemporaryDirectory(prefix="jarvis-chain-test-") as directory:
    os.environ["JARVIS_DATA_DIR"] = directory
    database.apply_migrations()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for index in range(3):
            audit_chain.append(connection, {"type": "fictional", "n": index})
        baseline = audit_chain.verify(connection)
        assert baseline["status"] == "LOCAL_CHAIN_CONSISTENT_UNANCHORED" and baseline["count"] == 3
        connection.execute("SAVEPOINT tamper")
        connection.execute("UPDATE audit_chain SET event_json=? WHERE seq=2", ('{}',))
        assert audit_chain.verify(connection)["status"] == "CHAIN_BROKEN"
        try:
            audit_chain.append(connection, {"type": "must-fail"})
        except audit_chain.AuditChainError:
            pass
        else:
            raise AssertionError("append accepted an already broken chain")
        connection.execute("ROLLBACK TO SAVEPOINT tamper")
        connection.execute("DELETE FROM audit_chain WHERE seq=2")
        assert audit_chain.verify(connection)["status"] == "CHAIN_GAP"
        connection.execute("ROLLBACK TO SAVEPOINT tamper")
        connection.execute("DELETE FROM audit_chain WHERE seq=3")
        # Without a trusted external head, deleting a suffix is indistinguishable.
        assert audit_chain.verify(connection)["status"] == "LOCAL_CHAIN_CONSISTENT_UNANCHORED"
        assert audit_chain.verify(connection)["head"] != baseline["head"]
        connection.execute("ROLLBACK TO SAVEPOINT tamper")
        # A writer can fabricate an entirely new internally consistent chain.
        row = connection.execute("SELECT event_json FROM audit_chain WHERE seq=1").fetchone()
        forged = json.loads(row[0]); forged["event"]["n"] = 99
        previous = audit_chain.GENESIS
        for index in range(1, 4):
            if index == 1:
                encoded = audit_chain.canonical_bytes(forged).decode("utf-8")
            else:
                encoded = connection.execute("SELECT event_json FROM audit_chain WHERE seq=?", (index,)).fetchone()[0]
            digest = audit_chain._hash(previous, encoded.encode("utf-8"))
            connection.execute("UPDATE audit_chain SET event_json=?,prev_hash=?,entry_hash=? WHERE seq=?", (encoded, previous, digest, index))
            previous = digest
        assert audit_chain.verify(connection)["status"] == "LOCAL_CHAIN_CONSISTENT_UNANCHORED"
        assert previous != baseline["head"]
        connection.execute("ROLLBACK TO SAVEPOINT tamper")
        connection.execute("RELEASE SAVEPOINT tamper")
        before = connection.execute("SELECT count(*) FROM m1_artifacts").fetchone()[0]
        audit_before = connection.execute("SELECT count(*) FROM audit_events").fetchone()[0]
        row = {"text": "fictional", "kind": "email", "received_at": "2026-01-01T00:00:00Z"}
        with patch.object(audit_chain, "append", side_effect=RuntimeError("fictional audit failure")):
            try:
                m1_pipeline.import_artifact(connection, row)
            except RuntimeError:
                pass
            else:
                raise AssertionError("audit failure did not reject write")
        assert connection.execute("SELECT count(*) FROM m1_artifacts").fetchone()[0] == before
        assert connection.execute("SELECT count(*) FROM audit_events").fetchone()[0] == audit_before
        assert audit_chain.verify(connection)["head"] == baseline["head"]
    def add(index):
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return audit_chain.append(connection, {"type": "concurrent", "n": index})["seq"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(add, range(8)))
    assert sorted(values) == list(range(4, 12))
    with database.connect() as connection:
        assert audit_chain.verify(connection)["count"] == 11
    worker = ("import sys;sys.path.insert(0,%r);from app import database;"
              "from usecases import audit_chain;"
              "cm=database.connect();c=cm.__enter__();c.execute('BEGIN IMMEDIATE');"
              "audit_chain.append(c,{'type':'process'});cm.__exit__(None,None,None)") % str(ROOT / "backend")
    processes = [subprocess.Popen([sys.executable, "-c", worker], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                 for _ in range(2)]
    for process in processes:
        _, errors = process.communicate(timeout=20)
        assert process.returncode == 0, errors.decode("utf-8", errors="replace")
    with database.connect() as connection:
        assert audit_chain.verify(connection)["count"] == 13

print("AUDIT_CANONICALIZATION_TAMPER_GAP_CONCURRENCY_ROLLBACK=PASS")
print("UNANCHORED_TAIL_DELETION_AND_RECOMPUTE_ARE_UNDETECTABLE=CONFIRMED")
