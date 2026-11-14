"""Unanchored local audit chain for synthetic M1 experiments only."""
import hashlib
import json
import re
import secrets
import unicodedata

DOMAIN = b"jarvis.audit.chain.v1\x00"
GENESIS = "0" * 64
HEX_ID = re.compile(r"[0-9a-f]{32}\Z")
HEX_HASH = re.compile(r"[0-9a-f]{64}\Z")


class AuditChainError(RuntimeError):
    pass


def _normalize(value):
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if not -(2**63) <= value < 2**63:
            raise ValueError("audit integer outside signed 64-bit range")
        return value
    if type(value) is str:
        normalized = unicodedata.normalize("NFC", value)
        normalized.encode("utf-8", errors="strict")
        return normalized
    if type(value) is list:
        return [_normalize(item) for item in value]
    if type(value) is dict:
        normalized = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("audit keys must be strings")
            key = _normalize(key)
            if key in normalized:
                raise ValueError("audit keys collide after NFC normalization")
            normalized[key] = _normalize(item)
        return normalized
    raise ValueError("unsupported audit value type")


def canonical_bytes(value):
    return json.dumps(_normalize(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(previous, event_bytes):
    if not isinstance(previous, str) or not HEX_HASH.fullmatch(previous):
        raise AuditChainError("invalid previous audit hash")
    return hashlib.sha256(DOMAIN + bytes.fromhex(previous) + event_bytes).hexdigest()


def _vault_meta_id(connection):
    has_meta = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vault_meta'").fetchone()
    stored = connection.execute("SELECT value FROM vault_meta WHERE name='vault_id'").fetchone() if has_meta else None
    return bool(has_meta), stored[0] if stored else None


def _identity(connection):
    identity = connection.execute("SELECT vault_id,epoch FROM audit_identity WHERE singleton=1").fetchone()
    has_meta, meta_id = _vault_meta_id(connection)
    if identity:
        vault_id, epoch = identity[0], identity[1]
        if has_meta and meta_id != vault_id:
            raise AuditChainError("vault identity does not match audit identity")
    else:
        vault_id = meta_id or secrets.token_hex(16)
        if has_meta and meta_id is None:
            connection.execute("INSERT INTO vault_meta(name,value) VALUES ('vault_id',?)", (vault_id,))
        epoch = secrets.token_hex(16)
        connection.execute("INSERT INTO audit_identity(singleton,vault_id,epoch) VALUES (1,?,?)", (vault_id, epoch))
    if not isinstance(vault_id, str) or not HEX_ID.fullmatch(vault_id) or not isinstance(epoch, str) or not HEX_ID.fullmatch(epoch):
        raise AuditChainError("invalid audit identity")
    return vault_id, epoch


def append(connection, event):
    """Append inside the caller's existing write transaction; never commit it."""
    if not connection.in_transaction:
        raise AuditChainError("audit append requires an existing write transaction")
    vault_id, epoch = _identity(connection)
    if verify(connection)["status"] != "LOCAL_CHAIN_CONSISTENT_UNANCHORED":
        raise AuditChainError("audit chain is invalid; refusing append")
    last = connection.execute("SELECT seq,entry_hash FROM audit_chain WHERE vault_id=? AND epoch=? ORDER BY seq DESC LIMIT 1", (vault_id, epoch)).fetchone()
    seq, previous = (last[0] + 1, last[1]) if last else (1, GENESIS)
    envelope = {"version": 1, "vault_id": vault_id, "epoch": epoch, "seq": seq, "event": event}
    encoded = canonical_bytes(envelope)
    entry_hash = _hash(previous, encoded)
    connection.execute("INSERT INTO audit_chain(vault_id,epoch,seq,event_json,prev_hash,entry_hash) VALUES (?,?,?,?,?,?)",
                       (vault_id, epoch, seq, encoded.decode("utf-8"), previous, entry_hash))
    return {"vault_id": vault_id, "epoch": epoch, "seq": seq, "head": entry_hash}


def verify(connection):
    """Check local structure only; tail deletion or full recomputation remains invisible."""
    identity = connection.execute("SELECT vault_id,epoch FROM audit_identity WHERE singleton=1").fetchone()
    if not identity:
        if connection.execute("SELECT 1 FROM audit_chain LIMIT 1").fetchone():
            return {"status": "CHAIN_BROKEN", "seq": 0}
        return {"status": "LOCAL_CHAIN_CONSISTENT_UNANCHORED", "count": 0, "head": GENESIS}
    vault_id, epoch = identity[0], identity[1]
    if not isinstance(vault_id, str) or not HEX_ID.fullmatch(vault_id) or not isinstance(epoch, str) or not HEX_ID.fullmatch(epoch):
        return {"status": "CHAIN_BROKEN", "seq": 0}
    has_meta, meta_id = _vault_meta_id(connection)
    if has_meta and meta_id != vault_id:
        return {"status": "CHAIN_BROKEN", "seq": 0}
    rows = connection.execute("SELECT vault_id,epoch,seq,event_json,prev_hash,entry_hash FROM audit_chain ORDER BY vault_id,epoch,seq").fetchall()
    previous = GENESIS
    for expected, row in enumerate(rows, 1):
        row_vault, row_epoch, seq, encoded, prev, digest = tuple(row)
        if (row_vault, row_epoch) != (vault_id, epoch):
            return {"status": "CHAIN_BROKEN", "seq": seq}
        if seq != expected:
            return {"status": "CHAIN_GAP", "seq": expected}
        try:
            event = json.loads(encoded, object_pairs_hook=_unique_keys)
            if (not isinstance(event, dict) or set(event) != {"version", "vault_id", "epoch", "seq", "event"}
                    or type(event["version"]) is not int or event["version"] != 1 or event["vault_id"] != vault_id or event["epoch"] != epoch
                    or type(event["seq"]) is not int or event["seq"] != seq
                    or canonical_bytes(event).decode("utf-8") != encoded or prev != previous or _hash(prev, encoded.encode("utf-8")) != digest):
                return {"status": "CHAIN_BROKEN", "seq": seq}
        except (ValueError, TypeError, UnicodeError, AuditChainError):
            return {"status": "CHAIN_BROKEN", "seq": seq}
        previous = digest
    return {"status": "LOCAL_CHAIN_CONSISTENT_UNANCHORED", "vault_id": vault_id, "epoch": epoch, "count": len(rows), "head": previous}


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result
