"""Synthetic signed checkpoints and local outbox; no persistent key or network."""
import copy
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["JARVIS_DATA_MODE"] = "synthetic"
for name in ("JARVIS_STORAGE_ADAPTER", "JARVIS_APSWMC_KEY", "JARVIS_VAULT_DIR"):
    os.environ.pop(name, None)

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from app import database
from usecases import audit_chain, audit_checkpoint, audit_outbox


def expect(error, call):
    try:
        call()
    except error:
        return
    raise AssertionError("expected " + error.__name__)


with tempfile.TemporaryDirectory(prefix="jarvis-checkpoint-") as directory:
    os.environ["JARVIS_DATA_DIR"] = directory
    database.apply_migrations()
    key = Ed25519PrivateKey.generate()  # ephemeral fictional key, never written
    public = key.public_key()
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        first = audit_chain.append(connection, {"type": "synthetic"})
    with database.connect() as connection:
        first_cp = audit_checkpoint.create(connection, key)
        assert audit_checkpoint.compare(connection, None, public)["status"] == "ANCHOR_STALE_OR_UNKNOWN"
        assert audit_checkpoint.compare(connection, first_cp, public)["status"] == "ANCHOR_STALE_OR_UNKNOWN"
        assert audit_checkpoint.compare(connection, first_cp, Ed25519PrivateKey.generate().public_key())["status"] == "ANCHOR_CONFLICT"
        tampered = copy.deepcopy(first_cp); tampered["body"]["head"] = "0" * 64
        assert audit_checkpoint.compare(connection, tampered, public)["status"] == "ANCHOR_CONFLICT"
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        audit_chain.append(connection, {"type": "synthetic_second"})
    with database.connect() as connection:
        second_cp = audit_checkpoint.create(connection, key, first_cp)
        assert second_cp["body"]["prev_digest"] == audit_checkpoint.digest(first_cp)
        assert audit_checkpoint.verify_history([first_cp, second_cp], public) == second_cp["body"]
        pin = audit_checkpoint.digest(second_cp)
        assert audit_checkpoint.compare_with_pinned_history(connection, [first_cp, second_cp], public, pinned_digest=pin)["status"] == "ANCHOR_STALE_OR_UNKNOWN"
        assert audit_checkpoint.compare_with_pinned_history(connection, [first_cp], public, pinned_digest=pin)["status"] == "ANCHOR_STALE_OR_UNKNOWN"
        assert audit_checkpoint.compare_with_pinned_history(connection, [second_cp, first_cp], public, pinned_digest=pin)["status"] == "ANCHOR_CONFLICT"
        expect(audit_checkpoint.CheckpointError, lambda: audit_checkpoint.verify_history([second_cp], public))
        expect(audit_checkpoint.CheckpointError, lambda: audit_checkpoint.verify_history([second_cp, first_cp], public))
        expect(audit_checkpoint.CheckpointError, lambda: audit_checkpoint.create(connection, key, second_cp))
        assert audit_checkpoint.compare(connection, first_cp, public)["local_seq"] == 2
        other_epoch = copy.deepcopy(second_cp)
        other_epoch["body"]["epoch"] = "f" * 32 if second_cp["body"]["epoch"] != "f" * 32 else "e" * 32
        other_epoch["signature"] = key.sign(audit_checkpoint.DOMAIN + audit_chain.canonical_bytes(other_epoch["body"])).hex()
        assert audit_checkpoint.compare(connection, other_epoch, public)["status"] == "ANCHOR_CONFLICT"
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("SAVEPOINT rollback_test")
        connection.execute("DELETE FROM audit_chain WHERE seq=2")
        assert audit_checkpoint.compare(connection, second_cp, public)["status"] == "ANCHOR_CONFLICT"
        connection.execute("ROLLBACK TO SAVEPOINT rollback_test")
        connection.execute("RELEASE SAVEPOINT rollback_test")
    snapshot = "a" * 32
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        result = audit_outbox.enqueue(connection, snapshot, second_cp, public)
        assert result == {"state": "pending", "attempts": 0}
        assert audit_outbox.enqueue(connection, snapshot, second_cp, public) == result
        expect(audit_outbox.OutboxError, lambda: audit_outbox.enqueue(connection, snapshot, first_cp, public))
        assert audit_outbox.record_outcome(connection, snapshot, expected_attempts=0, error="OFFLINE", next_attempt_at="2026-11-15T00:00:00Z")["state"] == "retry"
        expect(audit_outbox.OutboxError, lambda: audit_outbox.record_outcome(connection, snapshot, expected_attempts=0, receipt=audit_checkpoint.digest(second_cp)))
        expect(audit_outbox.OutboxError, lambda: audit_outbox.record_outcome(connection, snapshot, expected_attempts=1, receipt="0" * 64))
        done = audit_outbox.record_outcome(connection, snapshot, expected_attempts=1, receipt=audit_checkpoint.digest(second_cp))
        assert done == {"state": "succeeded", "attempts": 2}
        expect(audit_outbox.OutboxError, lambda: audit_outbox.record_outcome(connection, snapshot, expected_attempts=2, receipt=audit_checkpoint.digest(second_cp)))
        before = connection.execute("SELECT count(*) FROM audit_outbox").fetchone()[0]
        with patch.object(audit_chain, "append", side_effect=RuntimeError("fictional audit failure")):
            expect(RuntimeError, lambda: audit_outbox.enqueue(connection, "b" * 32, second_cp, public))
        assert connection.execute("SELECT count(*) FROM audit_outbox").fetchone()[0] == before
        pending_id = "d" * 32
        audit_outbox.enqueue(connection, pending_id, second_cp, public)
        with patch.object(audit_chain, "append", side_effect=RuntimeError("fictional audit failure")):
            expect(RuntimeError, lambda: audit_outbox.record_outcome(connection, pending_id, expected_attempts=0,
                                                                     receipt=audit_checkpoint.digest(second_cp)))
        assert connection.execute("SELECT state,attempts FROM audit_outbox WHERE snapshot_id=?", (pending_id,)).fetchone()[0] == "pending"
    with database.connect() as connection:
        assert audit_chain.verify(connection)["status"] == "LOCAL_CHAIN_CONSISTENT_UNANCHORED"

    # Simulate delivery followed by a crash before recording the outcome. The
    # fake receiver reconciles the same immutable digest on retry.
    crash_id = "c" * 32
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        audit_outbox.enqueue(connection, crash_id, second_cp, public)
    fake_remote = {crash_id: audit_checkpoint.digest(second_cp)}
    with database.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        assert audit_outbox.enqueue(connection, crash_id, second_cp, public)["state"] == "pending"
        audit_outbox.record_outcome(connection, crash_id, expected_attempts=0, receipt=fake_remote[crash_id])
    concurrent_id = "e" * 32
    def concurrent_enqueue(_):
        with database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return audit_outbox.enqueue(connection, concurrent_id, second_cp, public)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(concurrent_enqueue, range(2)))
    assert outcomes == [{"state": "pending", "attempts": 0}] * 2
    with database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM audit_outbox WHERE snapshot_id=?", (concurrent_id,)).fetchone()[0] == 1

print("CHECKPOINT_SIGNATURE_PREFIX_ROLLBACK_AND_UNKNOWN_FRESHNESS=PASS")
print("OUTBOX_IDEMPOTENCY_RETRY_RECEIPT_AND_AUDIT_ROLLBACK=PASS")
print("EXTERNAL_FRESHNESS_KEY_MANAGEMENT_AND_DISPATCH=NOT_IMPLEMENTED")
