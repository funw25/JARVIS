"""Experimental vault key lifecycle (S1): create, open, destroy, error classes.

Implements §3-§5 of docs/KEY_LIFECYCLE_AND_RECOVERY.md for synthetic data only:
a per-vault random data key wrapped by user-scoped DPAPI, a ``meta.json`` with a
``key_id``, and a fail-closed open path with stable error classes.

Not implemented here: passphrase/recovery-code wrappers, rotation, backup and
restore (S2/S3). Nothing in this module is used by the application.
"""
import json
import os
import secrets
import shutil
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from app.security.storage_policy import require_synthetic_storage
from . import apswmc_storage
from .windows_dpapi import protect_synthetic_key, unprotect_synthetic_key

FORMAT = "jarvis-vault-v1"
META = "meta.json"
KEYFILE = "dek.dpapi"
DBFILE = "vault.db"
RETIRED = "retired"


class VaultError(RuntimeError):
    """Base class for the vault lifecycle errors; ``code`` is stable for UI/tests."""

    code = "VAULT_ERROR"


class KeyMissing(VaultError):
    code = "KEY_MISSING"


class KeywrapMissing(VaultError):
    code = "KEYWRAP_MISSING"


class KeyUnwrapFailed(VaultError):
    code = "KEY_UNWRAP_FAILED"


class KeyMismatch(VaultError):
    code = "KEY_MISMATCH"


class FormatUnknown(VaultError):
    code = "FORMAT_UNKNOWN"


class DbCorrupt(VaultError):
    code = "DB_CORRUPT"


class DriverUnavailable(VaultError):
    code = "DRIVER_UNAVAILABLE"


def _require_driver():
    if apswmc_storage.apsw is None or "Multiple Ciphers" not in getattr(apswmc_storage.apsw, "mc_version", ""):
        raise DriverUnavailable("Isolated SQLite3MC driver unavailable")


def _unsafe(path):
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _meta_payload(key_id):
    return {
        "format": FORMAT,
        "key_id": key_id,
        "created_at": _now(),
        "rotated_at": None,
        "cipher": "chacha20",
    }


def _now():
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path, payload):
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(json.dumps(payload, indent=2).encode("utf-8"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _read_meta(root):
    meta_path = root / META
    if not meta_path.is_file() or _unsafe(meta_path):
        raise FormatUnknown("meta.json is missing or unsupported")
    try:
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FormatUnknown("meta.json is not readable JSON") from exc
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise FormatUnknown("unknown vault format")
    key_id = payload.get("key_id")
    if not isinstance(key_id, str) or len(key_id) != 16 or any(ch not in "0123456789abcdef" for ch in key_id):
        raise FormatUnknown("invalid key_id")
    if payload.get("cipher") != "chacha20":
        raise FormatUnknown("unsupported cipher")
    return payload


def create_vault(root, *, key_id=None):
    """Create an empty vault and return its metadata (key material stays wrapped)."""
    require_synthetic_storage()
    _require_driver()
    root = Path(root)
    parent = root.parent
    if not parent.is_dir():
        raise KeyMissing("vault parent directory does not exist")
    if os.path.lexists(root):
        raise VaultError("vault target already exists")

    staging = Path(tempfile.mkdtemp(prefix=".jarvis-vault-staging-", dir=parent))
    try:
        key = secrets.token_bytes(32)
        identifier = key_id or secrets.token_hex(8)
        wrapped = protect_synthetic_key(key)
        (staging / KEYFILE).write_bytes(wrapped)
        with apswmc_storage.connect(staging / DBFILE, key, create=True) as connection:
            connection.execute("CREATE TABLE vault_meta(name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO vault_meta VALUES ('format', ?)", (FORMAT,))
            connection.execute("INSERT INTO vault_meta VALUES ('key_id', ?)", (identifier,))
            connection.execute("INSERT INTO vault_meta VALUES ('created_at', ?)", (_now(),))
        if apswmc_storage.plaintext_header(staging / DBFILE):
            raise DbCorrupt("created vault is not encrypted")
        _write_json(staging / META, _meta_payload(identifier))
        if os.path.lexists(root):
            raise VaultError("vault target appeared during creation")
        os.replace(staging, root)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"root": str(root), "key_id": identifier, "format": FORMAT}


@contextmanager
def open_vault(root):
    """Eight-step fail-closed open; see §4 of the design document."""
    require_synthetic_storage()
    _require_driver()
    root = Path(root)
    if not root.is_dir() or _unsafe(root):
        raise KeyMissing("vault directory is missing or unsupported")
    meta = _read_meta(root)
    key_path = root / KEYFILE
    if not key_path.is_file() or _unsafe(key_path):
        raise KeywrapMissing("wrapped key file is missing")
    db_path = root / DBFILE
    if not db_path.is_file() or _unsafe(db_path):
        raise DbCorrupt("vault database is missing")

    try:
        wrapped = key_path.read_bytes()
    except OSError as exc:
        raise KeyUnwrapFailed("wrapped key is unreadable") from exc
    if not 1 <= len(wrapped) <= 16384:
        raise KeyUnwrapFailed("wrapped key has an invalid size")
    try:
        key = unprotect_synthetic_key(wrapped)
    except (OSError, ValueError) as exc:
        raise KeyUnwrapFailed("wrapped key could not be unwrapped") from exc

    try:
        with apswmc_storage.connect(db_path, key, create=False) as connection:
            try:
                stored = dict(connection.execute("SELECT name, value FROM vault_meta").fetchall())
            except Exception as exc:  # wrong key surfaces here on an existing file
                message = str(exc).lower()
                if "not a database" in message or "file is encrypted" in message:
                    raise KeyMismatch("key does not match the vault database") from exc
                raise DbCorrupt("vault database is unreadable") from exc
            if stored.get("format") != FORMAT:
                raise FormatUnknown("vault_meta format is unsupported")
            if stored.get("key_id") != meta["key_id"]:
                raise KeyMismatch("meta.json and database key_id differ")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise DbCorrupt("vault database failed integrity_check")
            yield connection
    except VaultError:
        raise
    except Exception as exc:  # noqa: BLE001 - classify driver failures into stable codes
        raise _classify_open_error(exc) from exc


def _classify_open_error(exc):
    """Map driver failures during open onto the stable vault error classes."""
    message = str(exc).lower()
    kind = type(exc).__name__.lower()
    if isinstance(exc, apswmc_storage.AdapterUnavailable):
        if "driver" in message or "cipher" in message or "key" in message:
            return KeyMismatch("key or cipher configuration was rejected")
        return DriverUnavailable(str(exc))
    if "notadb" in kind or "not a database" in message or "encrypted" in message or "key" in message:
        return KeyMismatch("key does not match the vault database")
    return DbCorrupt("vault database could not be opened")


def destroy_vault(root, *, confirm):
    """Remove a vault after the caller confirms its directory name."""
    require_synthetic_storage()
    root = Path(root)
    if confirm != root.name:
        raise VaultError("confirmation does not match the vault name")
    if not root.is_dir():
        raise KeyMissing("vault directory is missing")
    removed = []
    for name in (DBFILE, DBFILE + "-wal", DBFILE + "-shm", KEYFILE, META, META + ".tmp"):
        path = root / name
        if path.is_file():
            path.unlink()
            removed.append(name)
    retired = root / RETIRED
    if retired.is_dir():
        shutil.rmtree(retired)
        removed.append(RETIRED)
    try:
        root.rmdir()
        removed.append(".")
    except OSError:
        pass
    return removed


def vault_status(root):
    """Report which vault files exist; never returns key material."""
    root = Path(root)
    if not root.is_dir():
        return {"exists": False}
    status = {
        "exists": True,
        "meta": (root / META).is_file(),
        "key": (root / KEYFILE).is_file(),
        "database": (root / DBFILE).is_file(),
        "wal": (root / (DBFILE + "-wal")).is_file(),
    }
    if status["meta"]:
        try:
            status["key_id"] = _read_meta(root)["key_id"]
        except VaultError:
            status["key_id"] = None
    return status
