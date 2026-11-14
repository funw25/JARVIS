"""Verify S3 key rotation: K4, K5 and C2 (synthetic temporary vaults only)."""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".jarvis-python314-apswmc-probe"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(PROBE))

if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not PROBE.is_dir():
    print("VAULT_ROTATION=NOT_RUN (isolated Windows CPython 3.14 required)")
    raise SystemExit(2)
try:
    import apsw  # noqa: F401
except (ImportError, OSError):
    print("VAULT_ROTATION=NOT_RUN (candidate unavailable)")
    raise SystemExit(2)

os.environ["JARVIS_DATA_MODE"] = "synthetic"

from experimental import apswmc_storage, vault_backup, vault_keys as keys

MARKER = "FICTIONAL-ROTATION-" + os.urandom(6).hex()


def expect(error, call, label):
    try:
        result = call()
        if hasattr(result, "__enter__"):
            with result:
                pass
    except error:
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError("%s raised %s instead of %s" % (label, type(exc).__name__, error.__name__)) from exc
    raise AssertionError("%s did not raise %s" % (label, error.__name__))


def open_with_wrapper(root, wrapper_path):
    """Copy the vault and swap in another wrapper to prove which key works."""
    probe = Path(root).parent / (Path(root).name + "-probe-" + os.urandom(3).hex())
    shutil.copytree(root, probe)
    shutil.copy2(wrapper_path, probe / keys.KEYFILE)
    return keys.open_vault(probe), probe


with tempfile.TemporaryDirectory(prefix="jarvis-vault-rotation-") as directory:
    base = Path(directory)
    vault = base / "vault"
    info = keys.create_vault(vault)
    with keys.open_vault(vault) as connection:
        connection.execute("CREATE TABLE notes(value TEXT NOT NULL)")
        connection.execute("INSERT INTO notes VALUES (?)", (MARKER,))

    old_wrapper_bytes = (vault / keys.KEYFILE).read_bytes()
    rotated = keys.rotate_vault(vault)
    assert rotated["old_key_id"] == info["key_id"] and rotated["new_key_id"] != info["key_id"]

    with keys.open_vault(vault) as connection:
        assert connection.execute("SELECT value FROM notes").fetchone()[0] == MARKER
        stored = dict(connection.execute("SELECT name, value FROM vault_meta").fetchall())
    assert stored["key_id"] == rotated["new_key_id"], stored
    assert keys.vault_status(vault)["key_id"] == rotated["new_key_id"]
    assert not (vault / keys.PENDING).exists(), "pending marker survived a clean rotation"
    retired = vault / keys.RETIRED / ("dek.%s.dpapi" % rotated["old_key_id"])
    assert retired.is_file(), "old wrapper was not retired"
    print("K4_ROTATION_UPDATES_KEY_ID_AND_KEEPS_DATA=PASS")

    # The retired wrapper must no longer open the database.
    cm, probe = open_with_wrapper(vault, retired)
    try:
        expect(keys.KeyMismatch, lambda: cm.__enter__(), "old wrapper after rotation")
    finally:
        shutil.rmtree(probe, ignore_errors=True)
    print("K4_OLD_WRAPPER_NO_LONGER_OPENS=PASS")

    # K5: a failing rekey must leave the old key and metadata untouched.
    original_rekey = keys._rekey
    attempts = {"count": 0}

    def failing_rekey(connection, new_key):
        attempts["count"] += 1
        raise keys.RotateFailed("synthetic rekey failure")

    keys._rekey = failing_rekey
    try:
        expect(keys.RotateFailed, lambda: keys.rotate_vault(vault), "failed rotation")
    finally:
        keys._rekey = original_rekey
    assert attempts["count"] == 1, attempts
    with keys.open_vault(vault) as connection:
        assert connection.execute("SELECT value FROM notes").fetchone()[0] == MARKER
        stored = dict(connection.execute("SELECT name, value FROM vault_meta").fetchall())
    assert stored["key_id"] == rotated["new_key_id"], "failed rotation changed the key_id"
    assert not (vault / keys.PENDING).exists(), "failed rotation left a pending marker"
    assert keys.vault_status(vault)["key_id"] == rotated["new_key_id"]
    print("K5_FAILED_ROTATION_ROLLS_BACK_PACKAGING=PASS")

    # D5: the rotation reminder is driven by rotated_at and ages out after 180 days.
    fresh_status = keys.rotation_status(vault)
    assert fresh_status["age_days"] == 0 and fresh_status["due"] is False, fresh_status
    assert fresh_status["rotated_at"], "rotation did not record rotated_at"
    meta_payload = json.loads((vault / keys.META).read_text(encoding="utf-8"))
    meta_payload["rotated_at"] = "2025-01-01T00:00:00Z"
    (vault / keys.META).write_text(json.dumps(meta_payload), encoding="utf-8")
    aged = keys.rotation_status(vault)
    assert aged["due"] is True and aged["age_days"] > 180, aged
    print("D5_ROTATION_REMINDER_USES_ROTATED_AT=PASS age_days=%s" % aged["age_days"])
    meta_payload["rotated_at"] = fresh_status["rotated_at"]
    (vault / keys.META).write_text(json.dumps(meta_payload), encoding="utf-8")

    # C2: simulate a crash after the database was rekeyed but before the swap.
    old_key = keys._load_key(vault)
    new_key = os.urandom(32)
    new_id = os.urandom(8).hex()
    (vault / keys.NEXT_KEYFILE).write_bytes(keys.protect_synthetic_key(new_key))
    keys._write_json(vault / keys.NEXT_META, keys._meta_payload(new_id))
    keys._write_pending(vault, rotated["new_key_id"], new_id)
    connection = apswmc_storage.connect(vault / keys.DBFILE, old_key, create=False)
    try:
        handle = connection.__enter__()
        keys._rekey(handle, new_key)
        handle.execute("UPDATE vault_meta SET value=? WHERE name='key_id'", (new_id,))
        handle.commit()
    finally:
        connection.__exit__(None, None, None)
    assert (vault / keys.PENDING).is_file()

    with keys.open_vault(vault) as connection:  # resumes before opening
        assert connection.execute("SELECT value FROM notes").fetchone()[0] == MARKER
    assert not (vault / keys.PENDING).exists(), "resume did not clear the pending marker"
    assert keys.vault_status(vault)["key_id"] == new_id, keys.vault_status(vault)
    print("C2_INTERRUPTED_ROTATION_RESUMES_ON_NEXT_OPEN=PASS")

    # A backup taken after rotation still restores.
    made = vault_backup.create_backup(vault, base / "backups", passphrase="correct horse battery staple")
    vault_backup.restore_backup(made["archive"], base / "restored", passphrase="correct horse battery staple")
    with keys.open_vault(base / "restored") as connection:
        assert connection.execute("SELECT value FROM notes").fetchone()[0] == MARKER
    print("ROTATION_KEEPS_BACKUP_AND_RESTORE_WORKING=PASS")

print("VAULT_KEY_ROTATION=PASS")
print("PLANNED_ROTATION_AND_AUDIT_LOG=NOT_IMPLEMENTED")
