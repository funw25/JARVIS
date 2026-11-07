"""Verify the S1 vault key lifecycle: create, open, error classes and destroy.

Synthetic temporary data only. Requires Windows, CPython 3.14 and the isolated
SQLite3MC probe environment; otherwise it reports NOT_RUN and exits 2.
"""
import os
import secrets
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".jarvis-python314-apswmc-probe"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(PROBE))

if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not PROBE.is_dir():
    print("VAULT_KEY_LIFECYCLE=NOT_RUN (isolated Windows CPython 3.14 required)")
    raise SystemExit(2)

try:
    import apsw  # noqa: F401  (proves the probe environment is usable)
except (ImportError, OSError):
    print("VAULT_KEY_LIFECYCLE=NOT_RUN (candidate unavailable)")
    raise SystemExit(2)

os.environ["JARVIS_DATA_MODE"] = "synthetic"

from app.security.storage_policy import StorageSecurityError
from experimental import vault_keys as keys
from experimental.windows_dpapi import protect_synthetic_key


def expect(error, call, label):
    try:
        result = call()
        if hasattr(result, "__enter__"):  # open_vault returns a context manager
            with result:
                pass
    except error:
        return
    except Exception as exc:  # noqa: BLE001 - report the unexpected class
        raise AssertionError("%s raised %s instead of %s" % (label, type(exc).__name__, error.__name__)) from exc
    raise AssertionError("%s did not raise %s" % (label, error.__name__))


with tempfile.TemporaryDirectory(prefix="jarvis-vault-keys-", ignore_cleanup_errors=True) as directory:
    base = Path(directory)

    def fresh(name):
        root = base / name
        keys.create_vault(root)
        return root

    # K1: create produces a usable, encrypted, self-describing vault.
    root = base / "primary"
    info = keys.create_vault(root)
    assert len(info["key_id"]) == 16 and all(ch in "0123456789abcdef" for ch in info["key_id"])
    status = keys.vault_status(root)
    assert status["meta"] and status["key"] and status["database"]
    assert status["key_id"] == info["key_id"]
    with keys.open_vault(root) as connection:
        stored = dict(connection.execute("SELECT name, value FROM vault_meta").fetchall())
        assert stored["format"] == keys.FORMAT
        assert stored["key_id"] == info["key_id"]
    database_bytes = (root / keys.DBFILE).read_bytes()
    assert database_bytes[:16] != b"SQLite format 3\x00", "vault database is plaintext"
    assert keys.FORMAT.encode() not in database_bytes, "vault format leaked into ciphertext"
    with keys.open_vault(root) as connection:  # reopening stays possible
        assert connection.execute("SELECT count(*) FROM vault_meta").fetchone()[0] >= 2
    print("K1_CREATE_OPEN_AND_CIPHERTEXT=PASS")

    # K2: missing or damaged material fails closed with a stable class.
    expect(keys.KeyMissing, lambda: keys.open_vault(base / "does-not-exist"), "missing vault")
    missing_key = fresh("missing-key")
    (missing_key / keys.KEYFILE).unlink()
    expect(keys.KeywrapMissing, lambda: keys.open_vault(missing_key), "missing wrapped key")

    damaged = fresh("damaged-key")
    wrapped = (damaged / keys.KEYFILE).read_bytes()
    (damaged / keys.KEYFILE).write_bytes(bytes([wrapped[0] ^ 0xFF]) + wrapped[1:])
    expect(keys.KeyUnwrapFailed, lambda: keys.open_vault(damaged), "damaged wrapped key")

    bad_meta = fresh("bad-meta")
    (bad_meta / keys.META).write_text("{not json", encoding="utf-8")
    expect(keys.FormatUnknown, lambda: keys.open_vault(bad_meta), "unreadable meta")

    unknown_format = fresh("unknown-format")
    (unknown_format / keys.META).write_text('{"format":"jarvis-vault-v0","key_id":"0123456789abcdef","cipher":"chacha20"}', encoding="utf-8")
    expect(keys.FormatUnknown, lambda: keys.open_vault(unknown_format), "unknown format")

    no_database = fresh("no-database")
    (no_database / keys.DBFILE).unlink()
    expect(keys.DbCorrupt, lambda: keys.open_vault(no_database), "missing database")
    print("K2_MISSING_OR_DAMAGED_MATERIAL_FAILS_CLOSED=PASS")

    # K3: a valid wrapper for the wrong key is reported as KEY_MISMATCH.
    wrong_key = fresh("wrong-key")
    (wrong_key / keys.KEYFILE).write_bytes(protect_synthetic_key(secrets.token_bytes(32)))
    expect(keys.KeyMismatch, lambda: keys.open_vault(wrong_key), "wrong key")
    mismatch = fresh("mismatched-id")
    (mismatch / keys.META).write_text(
        '{"format":"jarvis-vault-v1","key_id":"fedcba9876543210","cipher":"chacha20"}', encoding="utf-8"
    )
    expect(keys.KeyMismatch, lambda: keys.open_vault(mismatch), "key_id mismatch")
    print("K3_WRONG_KEY_AND_KEY_ID_MISMATCH=PASS")

    # K6: destroy needs the exact name and leaves nothing behind.
    doomed = fresh("doomed-vault")
    expect(keys.VaultError, lambda: keys.destroy_vault(doomed, confirm="wrong-name"), "bad confirmation")
    assert (doomed / keys.DBFILE).is_file(), "wrong confirmation still deleted files"
    removed = keys.destroy_vault(doomed, confirm="doomed-vault")
    assert "vault.db" in removed and "meta.json" in removed
    assert not doomed.exists(), "vault directory survived destroy"
    expect(keys.KeyMissing, lambda: keys.open_vault(doomed), "open after destroy")
    print("K6_DESTROY_REQUIRES_CONFIRMATION_AND_IS_FINAL=PASS")

    # Guards: the synthetic gate applies, and creation refuses unsafe targets.
    os.environ.pop("JARVIS_DATA_MODE", None)
    expect(StorageSecurityError, lambda: keys.create_vault(base / "blocked"), "create without gate")
    assert not (base / "blocked").exists(), "blocked create left a directory"
    expect(StorageSecurityError, lambda: keys.open_vault(root), "open without gate")
    os.environ["JARVIS_DATA_MODE"] = "synthetic"
    expect(keys.VaultError, lambda: keys.create_vault(root), "create over existing vault")
    expect(keys.KeyMissing, lambda: keys.create_vault(base / "absent-parent" / "vault"), "create in missing parent")
    print("GATE_AND_CREATE_GUARDS=PASS")

    # Cleanup should not leave the database file locked.
    closed = base / "closed"
    with keys.open_vault(root) as connection:
        held = connection
    expect(Exception, lambda: held.execute("SELECT 1"), "connection after close")
    print("CONNECTION_CLOSED_AFTER_OPEN=PASS")

print("VAULT_KEY_LIFECYCLE=PASS")
print("PASSPHRASE_RECOVERY_ROTATION_AND_BACKUP=NOT_IMPLEMENTED")
