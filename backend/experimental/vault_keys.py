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
NEXT_KEYFILE = "dek.next.dpapi"
NEXT_META = "meta.next.json"
PENDING = "pending.json"


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


class RotateFailed(VaultError):
    code = "ROTATE_FAILED"


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


def write_key_file(root, key):
    """Wrap ``key`` with DPAPI into ``root/dek.dpapi`` (atomic rename)."""
    root = Path(root)
    wrapped = protect_synthetic_key(key)
    temporary = root / (KEYFILE + ".tmp")
    temporary.write_bytes(wrapped)
    os.replace(temporary, root / KEYFILE)


def write_meta(root, key_id):
    """Write ``meta.json`` atomically for ``key_id``."""
    _write_json(Path(root) / META, _meta_payload(key_id))


def _load_key(root):
    """Unwrap the stored data key (never cache it beyond the call)."""
    key_path = Path(root) / KEYFILE
    if not key_path.is_file() or _unsafe(key_path):
        raise KeywrapMissing("wrapped key file is missing")
    try:
        wrapped = key_path.read_bytes()
    except OSError as exc:
        raise KeyUnwrapFailed("wrapped key is unreadable") from exc
    if not 1 <= len(wrapped) <= 16384:
        raise KeyUnwrapFailed("wrapped key has an invalid size")
    try:
        return unprotect_synthetic_key(wrapped)
    except (OSError, ValueError) as exc:
        raise KeyUnwrapFailed("wrapped key could not be unwrapped") from exc


def _rekey(connection, new_key):
    """Ask the driver to re-encrypt the database under ``new_key``."""
    connection.commit()
    result = connection.pragma("rekey", "raw:" + new_key.hex())
    if str(result).lower() != "ok":
        raise RotateFailed("driver refused the rekey operation")
    connection.commit()


def _write_pending(root, old_key_id, new_key_id):
    _write_json(Path(root) / PENDING, {
        "format": "jarvis-rotation-v1",
        "old_key_id": old_key_id,
        "new_key_id": new_key_id,
        "created_at": _now(),
    })


def rotate_vault(root):
    """Rotate the data key; either every artefact ends up on the new key or none does."""
    require_synthetic_storage()
    _require_driver()
    root = Path(root)
    meta = _read_meta(root)
    old_key_id = meta["key_id"]
    old_key = _load_key(root)

    new_key = secrets.token_bytes(32)
    new_key_id = secrets.token_hex(8)
    (root / RETIRED).mkdir(exist_ok=True)

    # Record the new wrapper and metadata first; the pending marker makes an
    # interrupted rotation recoverable instead of leaving a silent mismatch.
    wrapped_new = protect_synthetic_key(new_key)
    (root / NEXT_KEYFILE).write_bytes(wrapped_new)
    _write_json(root / NEXT_META, _meta_payload(new_key_id))
    _write_pending(root, old_key_id, new_key_id)

    try:
        connection = apswmc_storage.connect(root / DBFILE, old_key, create=False)
        try:
            handle = connection.__enter__()
            _rekey(handle, new_key)
            handle.execute("UPDATE vault_meta SET value=? WHERE name='key_id'", (new_key_id,))
            handle.commit()
        finally:
            connection.__exit__(None, None, None)
    except Exception as exc:  # noqa: BLE001 - roll the rotation back when possible
        try:
            restore = apswmc_storage.connect(root / DBFILE, new_key, create=False)
            try:
                handle = restore.__enter__()
                _rekey(handle, old_key)
                handle.execute("UPDATE vault_meta SET value=? WHERE name='key_id'", (old_key_id,))
                handle.commit()
            finally:
                restore.__exit__(None, None, None)
        except Exception:  # pragma: no cover - leaves the pending marker for resume
            pass
        else:
            for name in (NEXT_KEYFILE, NEXT_META, PENDING):
                (root / name).unlink(missing_ok=True)
        raise RotateFailed("rotation failed and was rolled back") from exc

    _finish_rotation(root, old_key_id, new_key_id)
    return {"root": str(root), "old_key_id": old_key_id, "new_key_id": new_key_id}


def _finish_rotation(root, old_key_id, new_key_id):
    """Verify both directions, retire the old wrapper and swap in the new one."""
    root = Path(root)
    new_key = _load_key_from(root / NEXT_KEYFILE)
    _assert_opens(root, new_key, new_key_id)
    old_key = _load_key(root)
    if _opens(root, old_key):
        raise RotateFailed("old key still opens the database after rekey")

    retired = root / RETIRED
    retired.mkdir(exist_ok=True)
    (retired / ("dek.%s.dpapi" % old_key_id)).write_bytes((root / KEYFILE).read_bytes())
    os.replace(root / NEXT_KEYFILE, root / KEYFILE)
    os.replace(root / NEXT_META, root / META)
    (root / PENDING).unlink(missing_ok=True)


def _load_key_from(path):
    try:
        return unprotect_synthetic_key(Path(path).read_bytes())
    except (OSError, ValueError) as exc:
        raise KeyUnwrapFailed("wrapped key could not be unwrapped") from exc


def _opens(root, key):
    try:
        connection = apswmc_storage.connect(Path(root) / DBFILE, key, create=False)
        try:
            handle = connection.__enter__()
            handle.execute("SELECT count(*) FROM vault_meta").fetchone()
            return True
        finally:
            connection.__exit__(None, None, None)
    except Exception:  # noqa: BLE001 - any failure means the key does not open it
        return False


def _assert_opens(root, key, key_id):
    if not _opens(root, key):
        raise RotateFailed("new key does not open the rotated database")
    connection = apswmc_storage.connect(Path(root) / DBFILE, key, create=False)
    try:
        handle = connection.__enter__()
        stored = dict(handle.execute("SELECT name, value FROM vault_meta").fetchall())
    finally:
        connection.__exit__(None, None, None)
    if stored.get("key_id") != key_id:
        raise RotateFailed("rotated database has an unexpected key_id")


def resume_rotation(root):
    """Complete or discard an interrupted rotation; returns the action taken."""
    root = Path(root)
    pending_path = root / PENDING
    if not pending_path.is_file():
        return None
    try:
        pending = json.loads(pending_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise FormatUnknown("pending rotation marker is unreadable") from exc
    old_key_id, new_key_id = pending.get("old_key_id"), pending.get("new_key_id")
    if not (isinstance(old_key_id, str) and isinstance(new_key_id, str)):
        raise FormatUnknown("pending rotation marker is incomplete")

    if (root / NEXT_KEYFILE).is_file():
        try:
            new_key = _load_key_from(root / NEXT_KEYFILE)
        except KeyUnwrapFailed:
            new_key = None
        if new_key is not None and _opens(root, new_key):
            _finish_rotation(root, old_key_id, new_key_id)
            return "completed"
    # The database still uses the old key: drop the abandoned rotation artefacts.
    for name in (NEXT_KEYFILE, NEXT_META, PENDING):
        (root / name).unlink(missing_ok=True)
    return "discarded"


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
        write_key_file(staging, key)
        with apswmc_storage.connect(staging / DBFILE, key, create=True) as connection:
            connection.execute("CREATE TABLE vault_meta(name TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO vault_meta VALUES ('format', ?)", (FORMAT,))
            connection.execute("INSERT INTO vault_meta VALUES ('key_id', ?)", (identifier,))
            connection.execute("INSERT INTO vault_meta VALUES ('created_at', ?)", (_now(),))
        if apswmc_storage.plaintext_header(staging / DBFILE):
            raise DbCorrupt("created vault is not encrypted")
        write_meta(staging, identifier)
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
    if (root / PENDING).is_file():
        resume_rotation(root)
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

    # Only the open sequence itself is classified; exceptions raised by the
    # caller's body must propagate unchanged, so the context manager is driven
    # manually instead of wrapping the yield in the same try block.
    connection_cm = apswmc_storage.connect(db_path, key, create=False)
    try:
        connection = connection_cm.__enter__()
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
    except VaultError:
        connection_cm.__exit__(None, None, None)
        raise
    except Exception as exc:  # noqa: BLE001 - classify driver failures into stable codes
        try:
            connection_cm.__exit__(None, None, None)
        except Exception:  # pragma: no cover - best effort while failing closed
            pass
        raise _classify_open_error(exc) from exc

    try:
        yield connection
    finally:
        connection_cm.__exit__(None, None, None)


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
