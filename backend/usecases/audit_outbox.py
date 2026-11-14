"""Transactional synthetic checkpoint intents; no dispatcher or remote access."""
import json
from contextlib import contextmanager
from datetime import datetime

from . import audit_chain, audit_checkpoint

ACTION = "checkpoint_publish"
ERRORS = {"OFFLINE", "TRANSIENT", "CONFLICT"}


class OutboxError(RuntimeError):
    pass


@contextmanager
def _atomic(connection):
    if not connection.in_transaction:
        raise OutboxError("outbox requires a caller-owned transaction")
    connection.execute("SAVEPOINT audit_outbox_write")
    try:
        yield
        connection.execute("RELEASE SAVEPOINT audit_outbox_write")
    except BaseException:
        connection.execute("ROLLBACK TO SAVEPOINT audit_outbox_write")
        connection.execute("RELEASE SAVEPOINT audit_outbox_write")
        raise


def enqueue(connection, snapshot_id, checkpoint, public_key):
    if not isinstance(snapshot_id, str) or not audit_chain.HEX_ID.fullmatch(snapshot_id):
        raise OutboxError("invalid synthetic snapshot identifier")
    audit_checkpoint.verify_signature(checkpoint, public_key)
    payload = audit_chain.canonical_bytes(checkpoint).decode("utf-8")
    with _atomic(connection):
        comparison = audit_checkpoint.compare(connection, checkpoint, public_key)
        if "checkpoint_seq" not in comparison:
            raise OutboxError("checkpoint is not a verified local prefix")
        existing = connection.execute("SELECT payload_json,state,attempts FROM audit_outbox WHERE snapshot_id=? AND action=?", (snapshot_id, ACTION)).fetchone()
        if existing:
            if existing[0] != payload:
                raise OutboxError("idempotency key already has different content")
            return {"state": existing[1], "attempts": existing[2]}
        audit_chain.append(connection, {"type": "checkpoint_publish_intent", "snapshot_id": snapshot_id,
                                        "checkpoint_digest": audit_checkpoint.digest(checkpoint)})
        connection.execute("INSERT INTO audit_outbox(snapshot_id,action,payload_json,state,attempts) VALUES (?,?,?,'pending',0)",
                           (snapshot_id, ACTION, payload))
        return {"state": "pending", "attempts": 0}


def record_outcome(connection, snapshot_id, *, expected_attempts, receipt=None, error=None, next_attempt_at=None):
    if type(expected_attempts) is not int or expected_attempts < 0:
        raise OutboxError("invalid attempt number")
    if (receipt is None) == (error is None):
        raise OutboxError("supply exactly one outcome")
    if receipt is not None and (not isinstance(receipt, str) or not audit_chain.HEX_HASH.fullmatch(receipt) or next_attempt_at is not None):
        raise OutboxError("receipt must be a digest and must not schedule a retry")
    if error is not None:
        if not isinstance(error, str) or error not in ERRORS or not isinstance(next_attempt_at, str):
            raise OutboxError("retry needs a bounded error code and timestamp")
        try:
            parsed = datetime.fromisoformat(next_attempt_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError("timezone required")
        except ValueError as exc:
            raise OutboxError("retry timestamp invalid") from exc
    with _atomic(connection):
        row = connection.execute("SELECT state,attempts,payload_json FROM audit_outbox WHERE snapshot_id=? AND action=?", (snapshot_id, ACTION)).fetchone()
        if row is None or row[0] not in ("pending", "retry") or row[1] != expected_attempts:
            raise OutboxError("outbox state or attempt number changed")
        if receipt is not None and receipt != audit_checkpoint.digest(json.loads(row[2])):
            raise OutboxError("receipt does not match the queued checkpoint digest")
        state = "succeeded" if receipt is not None else "retry"
        audit_chain.append(connection, {"type": "checkpoint_publish_outcome", "snapshot_id": snapshot_id,
                                        "state": state, "attempt": expected_attempts + 1,
                                        "receipt": receipt, "error": error})
        updated = connection.execute(
            "UPDATE audit_outbox SET state=?,attempts=attempts+1,next_attempt_at=?,last_error=?,receipt=? "
            "WHERE snapshot_id=? AND action=? AND attempts=? AND state IN ('pending','retry')",
            (state, next_attempt_at, error, receipt, snapshot_id, ACTION, expected_attempts))
        if updated.rowcount != 1:
            raise OutboxError("outbox state changed concurrently")
        return {"state": state, "attempts": expected_attempts + 1}
