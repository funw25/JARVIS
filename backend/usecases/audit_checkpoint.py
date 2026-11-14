"""Signed checkpoint primitives for synthetic local experiments.

Key generation, storage, freshness authorities and external dispatch are absent.
Caller-supplied keys and checkpoints are not automatically trusted.
"""
import hashlib

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import audit_chain

DOMAIN = b"jarvis.audit.checkpoint.v1\x00"
FIELDS = {"version", "vault_id", "epoch", "seq", "head", "prev_digest"}


class CheckpointError(RuntimeError):
    pass


def _body(value):
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise CheckpointError("malformed checkpoint body")
    if (type(value["version"]) is not int or value["version"] != 1 or
            type(value["seq"]) is not int or value["seq"] < 1 or
            not isinstance(value["vault_id"], str) or not audit_chain.HEX_ID.fullmatch(value["vault_id"]) or
            not isinstance(value["epoch"], str) or not audit_chain.HEX_ID.fullmatch(value["epoch"]) or
            not isinstance(value["head"], str) or not audit_chain.HEX_HASH.fullmatch(value["head"]) or
            not isinstance(value["prev_digest"], str) or not audit_chain.HEX_HASH.fullmatch(value["prev_digest"])):
        raise CheckpointError("invalid checkpoint fields")
    try:
        return audit_chain.canonical_bytes(value)
    except (ValueError, UnicodeError) as exc:
        raise CheckpointError("checkpoint encoding invalid") from exc


def verify_signature(checkpoint, public_key):
    if not isinstance(public_key, Ed25519PublicKey) or not isinstance(checkpoint, dict) or set(checkpoint) != {"body", "signature"}:
        raise CheckpointError("invalid checkpoint or public key")
    encoded = _body(checkpoint["body"])
    signature = checkpoint["signature"]
    if not isinstance(signature, str) or len(signature) != 128:
        raise CheckpointError("invalid checkpoint signature")
    try:
        raw = bytes.fromhex(signature)
        if raw.hex() != signature:
            raise ValueError("non-canonical signature")
        public_key.verify(raw, DOMAIN + encoded)
    except (ValueError, InvalidSignature) as exc:
        raise CheckpointError("checkpoint signature does not verify") from exc
    return checkpoint["body"]


def digest(checkpoint):
    return hashlib.sha256(DOMAIN + audit_chain.canonical_bytes(checkpoint)).hexdigest()


def verify_history(checkpoints, public_key):
    """Check a complete same-epoch signed history; freshness is not established."""
    if not checkpoints:
        raise CheckpointError("checkpoint history is empty")
    previous_digest, previous_body = audit_chain.GENESIS, None
    for checkpoint in checkpoints:
        body = verify_signature(checkpoint, public_key)
        if body["prev_digest"] != previous_digest:
            raise CheckpointError("checkpoint history is incomplete or reordered")
        if previous_body is not None and (
                (body["vault_id"], body["epoch"]) != (previous_body["vault_id"], previous_body["epoch"]) or
                body["seq"] <= previous_body["seq"]):
            raise CheckpointError("checkpoint identity changed or sequence did not advance")
        previous_digest, previous_body = digest(checkpoint), body
    return previous_body


def create(connection, private_key, previous=None):
    """Sign an already committed chain state; the caller supplies a test key."""
    if not isinstance(private_key, Ed25519PrivateKey):
        raise CheckpointError("Ed25519 private key required")
    if connection.in_transaction:
        raise CheckpointError("checkpoint creation requires a committed chain")
    # A single read transaction keeps the full-chain verification and prefix read consistent.
    connection.execute("BEGIN")
    try:
        current = audit_chain.verify(connection)
        if current["status"] != "LOCAL_CHAIN_CONSISTENT_UNANCHORED" or current["count"] < 1:
            raise CheckpointError("a valid nonempty local chain is required")
        prev_digest = audit_chain.GENESIS
        if previous is not None:
            old = verify_signature(previous, private_key.public_key())
            if (old["vault_id"], old["epoch"]) != (current["vault_id"], current["epoch"]):
                raise CheckpointError("previous checkpoint identity conflicts")
            if old["seq"] >= current["count"]:
                raise CheckpointError("checkpoint must advance the chain")
            row = connection.execute("SELECT entry_hash FROM audit_chain WHERE vault_id=? AND epoch=? AND seq=?",
                                     (old["vault_id"], old["epoch"], old["seq"])).fetchone()
            if row is None or row[0] != old["head"]:
                raise CheckpointError("previous checkpoint is not a local prefix")
            prev_digest = digest(previous)
        body = {"version": 1, "vault_id": current["vault_id"], "epoch": current["epoch"],
                "seq": current["count"], "head": current["head"], "prev_digest": prev_digest}
        return {"body": body, "signature": private_key.sign(DOMAIN + _body(body)).hex()}
    finally:
        connection.rollback()


def compare(connection, checkpoint, public_key):
    """Compare a supplied checkpoint; never assert its freshness or authenticity of its source."""
    owned = not connection.in_transaction
    if owned:
        connection.execute("BEGIN")
    try:
        return _compare(connection, checkpoint, public_key)
    finally:
        if owned:
            connection.rollback()


def _compare(connection, checkpoint, public_key):
    local = audit_chain.verify(connection)
    if local["status"] in {"CHAIN_BROKEN", "CHAIN_GAP"}:
        return local
    if checkpoint is None or public_key is None:
        return {"status": "ANCHOR_STALE_OR_UNKNOWN", "reason": "checkpoint or trusted key unavailable"}
    try:
        remote = verify_signature(checkpoint, public_key)
    except CheckpointError:
        return {"status": "ANCHOR_CONFLICT", "reason": "invalid checkpoint signature or format"}
    if (local.get("vault_id"), local.get("epoch")) != (remote["vault_id"], remote["epoch"]):
        return {"status": "ANCHOR_CONFLICT", "reason": "vault or epoch mismatch"}
    if local["count"] < remote["seq"]:
        return {"status": "ANCHOR_CONFLICT", "reason": "local chain is behind supplied checkpoint"}
    row = connection.execute("SELECT entry_hash FROM audit_chain WHERE vault_id=? AND epoch=? AND seq=?",
                             (remote["vault_id"], remote["epoch"], remote["seq"])).fetchone()
    if row is None or row[0] != remote["head"]:
        return {"status": "ANCHOR_CONFLICT", "reason": "checkpoint is not a local prefix"}
    return {"status": "ANCHOR_STALE_OR_UNKNOWN", "reason": "signature and prefix match; checkpoint freshness unverified",
            "checkpoint_seq": remote["seq"], "local_seq": local["count"]}
