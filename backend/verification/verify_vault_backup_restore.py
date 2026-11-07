"""Verify S2 backup archives: wrap, restore, tampering and guards (synthetic only).

Covers B1-B4, B8-B10 of the acceptance matrix; B5 (concurrent writes during a
backup) and the upload paths (B6/B7) are separate steps.
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".jarvis-python314-apswmc-probe"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(PROBE))

if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not PROBE.is_dir():
    print("VAULT_BACKUP=NOT_RUN (isolated Windows CPython 3.14 required)")
    raise SystemExit(2)
try:
    import apsw  # noqa: F401
except (ImportError, OSError):
    print("VAULT_BACKUP=NOT_RUN (candidate unavailable)")
    raise SystemExit(2)

os.environ["JARVIS_DATA_MODE"] = "synthetic"

from experimental import vault_backup as backup
from experimental import vault_keys as keys

MARKER = "FICTIONAL-BACKUP-MARKER-" + os.urandom(6).hex()


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


def rebuild(source, target, mutate):
    """Copy a zip, letting ``mutate`` replace or drop members."""
    with zipfile.ZipFile(source) as src, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            payload = src.read(name)
            replacement = mutate(name, payload)
            if replacement is None:
                continue
            dst.writestr(name, replacement)


with tempfile.TemporaryDirectory(prefix="jarvis-vault-backup-", ignore_cleanup_errors=True) as directory:
    base = Path(directory)
    vault = base / "vault"
    info = keys.create_vault(vault)
    with keys.open_vault(vault) as connection:
        connection.execute("CREATE TABLE notes(value TEXT NOT NULL)")
        connection.execute("INSERT INTO notes VALUES (?)", (MARKER,))

    archives = base / "backups"
    passphrase = "correct horse battery staple"
    result = backup.create_backup(vault, archives, passphrase=passphrase)
    archive = Path(result["archive"])
    assert archive.is_file() and archive.suffix == backup.SUFFIX
    assert result["recovery_code"], "a recovery code should be generated when not supplied"
    assert backup.inspect_archive(archive)["entries"] and not backup.inspect_archive(archive)["suspicious"]
    print("B1_ARCHIVE_CREATED_WITH_WRAPPED_KEY_AND_MANIFEST=PASS")

    # B2: the archive database is encrypted under its own key.
    with zipfile.ZipFile(archive) as bundle:
        database = bundle.read(backup.DB)
        manifest = json.loads(bundle.read(backup.MANIFEST))
    assert database[:16] != b"SQLite format 3\x00", "backup database is plaintext"
    assert MARKER.encode() not in database, "marker leaked into the backup database"
    assert manifest["source_key_id"] == info["key_id"]
    in_place = base / "extracted.db"
    in_place.write_bytes(database)
    with sqlite3.connect(in_place) as plain:
        expect(sqlite3.DatabaseError, lambda: plain.execute("SELECT count(*) FROM sqlite_master").fetchone(), "plain sqlite on backup")
    print("B2_BACKUP_USES_INDEPENDENT_KEY_AND_REJECTS_PLAIN_SQLITE=PASS")

    # Restore with the passphrase after removing the original vault.
    keys.destroy_vault(vault, confirm="vault")
    restored = keys.restore = backup.restore_backup(archive, base / "restored", passphrase=passphrase)
    with keys.open_vault(base / "restored") as connection:
        assert connection.execute("SELECT value FROM notes").fetchone()[0] == MARKER
    assert restored["key_id"] == info["key_id"]
    print("B1_RESTORE_WITH_PASSPHRASE_RECOVERS_DATA=PASS")

    # Restore with the recovery code into a second location.
    backup.restore_backup(archive, base / "restored-code", recovery_code=result["recovery_code"])
    with keys.open_vault(base / "restored-code") as connection:
        assert connection.execute("SELECT value FROM notes").fetchone()[0] == MARKER
    print("B1_RESTORE_WITH_RECOVERY_CODE_RECOVERS_DATA=PASS")

    # B3: a single flipped database byte is rejected.
    tampered = base / "tampered.jvault"
    rebuild(archive, tampered, lambda name, data: (bytes([data[0] ^ 0xFF]) + data[1:]) if name == backup.DB else data)
    expect(backup.ArchiveRejected, lambda: backup.restore_backup(tampered, base / "tampered-out", passphrase=passphrase), "tampered database")

    # B4: a mismatched manifest key_id is rejected.
    def mutate_manifest(name, data):
        if name != backup.MANIFEST:
            return data
        payload = json.loads(data)
        payload["source_key_id"] = "0123456789abcdef"
        return json.dumps(payload).encode("utf-8")

    mismatched = base / "mismatched.jvault"
    rebuild(archive, mismatched, mutate_manifest)
    expect(backup.ArchiveRejected, lambda: backup.restore_backup(mismatched, base / "mismatch-out", passphrase=passphrase), "manifest mismatch")
    print("B3_B4_TAMPERED_BYTES_AND_MISMATCHED_MANIFEST_REJECTED=PASS")

    # B8: passphrase policy.
    expect(backup.PassphraseRejected, lambda: backup.create_backup(vault if vault.exists() else base / "restored", base / "short", passphrase="short-pass"), "short passphrase")
    expect(backup.PassphraseRejected, lambda: backup.create_backup(base / "restored", base / "weak", passphrase="password1234"), "common passphrase")
    print("B8_PASSPHRASE_POLICY_ENFORCED=PASS")

    # B9: KDF parameters below the floor are refused.
    def weaken_kdf(name, data):
        if name != backup.KEYWRAP:
            return data
        payload = json.loads(data)
        for entry in payload["wraps"]:
            entry["kdf"]["m"] = 1024
            entry["kdf"]["t"] = 1
        return json.dumps(payload).encode("utf-8")

    weak = base / "weak-kdf.jvault"
    rebuild(archive, weak, weaken_kdf)
    expect(backup.KdfParamsRejected, lambda: backup.restore_backup(weak, base / "weak-out", passphrase=passphrase), "downgraded KDF")
    print("B9_KDF_DOWNGRADE_REJECTED=PASS")

    # B10: missing or damaged key wrap is reported distinctly.
    without_wrap = base / "no-wrap.jvault"
    rebuild(archive, without_wrap, lambda name, data: None if name == backup.KEYWRAP else data)
    expect(backup.ArchiveRejected, lambda: backup.restore_backup(without_wrap, base / "nowrap-out", passphrase=passphrase), "missing key wrap")

    broken_wrap = base / "broken-wrap.jvault"
    def break_wrap(name, data):
        if name != backup.KEYWRAP:
            return data
        payload = json.loads(data)
        entry = payload["wraps"][0]
        entry["ciphertext"] = ("00" * 16) + entry["ciphertext"][32:]
        return json.dumps(payload).encode("utf-8")

    rebuild(archive, broken_wrap, break_wrap)
    expect(backup.UnwrapFailed, lambda: backup.restore_backup(broken_wrap, base / "broken-out", passphrase=passphrase), "damaged key wrap")

    expect(backup.UnwrapFailed, lambda: backup.restore_backup(archive, base / "wrong-pass", passphrase="wrong passphrase value"), "wrong passphrase")
    print("B10_MISSING_OR_DAMAGED_KEY_WRAP_AND_WRONG_SECRET=PASS")

    # Upload guard: an archive carrying recovery material is refused.
    leaky = base / "leaky.jvault"
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(leaky, "w", zipfile.ZIP_DEFLATED) as dst:
        for name in src.namelist():
            dst.writestr(name, src.read(name))
        dst.writestr("recovery_code.txt", b"should never travel")
    expect(backup.ArchiveRejected, lambda: backup.assert_upload_safe(leaky), "upload guard")

    expect(backup.RestoreRejected, lambda: backup.restore_backup(archive, base / "restored", passphrase=passphrase), "restore over existing vault")
    print("UPLOAD_GUARD_AND_NO_OVERWRITE=PASS")

    print("B5_CONCURRENT_WRITE_SNAPSHOT=SKIPPED_IN_THIS_STEP")

print("VAULT_BACKUP_ARCHIVE_AND_RESTORE=PASS")
print("S3_UPLOAD_AND_REMOTE_RESTORE=NOT_IMPLEMENTED")
