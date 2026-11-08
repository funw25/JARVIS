"""S2: backup archives and restore for the experimental vault (synthetic only).

An archive (``.jvault``) is self-contained: an encrypted database copy under a
fresh random backup key, that key wrapped twice (passphrase and recovery code),
and a manifest with hashes and KDF parameters. The passphrase and the recovery
code themselves never enter the archive.

See docs/KEY_LIFECYCLE_AND_RECOVERY.md §6. Not used by the application.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from argon2 import low_level
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import apswmc_storage, vault_keys
from .windows_dpapi import protect_synthetic_key

FORMAT = "jarvis-backup-v1"
KEYWRAP_FORMAT = "jarvis-backup-keywrap-v1"
SUFFIX = ".jvault"
DB = "vault.db"
KEYWRAP = "key.bkwrap"
MANIFEST = "manifest.json"
ALLOWED = {DB, KEYWRAP, MANIFEST}
MIN_PASSPHRASE = 12
ARGON2 = {"name": "argon2id", "m": 65536, "t": 3, "p": 2}
FORBIDDEN = ("passphrase", "recovery", "secret", "plain", "dek.dpapi", "key.dpapi")
COMMON_PASSPHRASES = {
    "password1234", "passwordpassword", "123456789012", "qwertyuiop12",
    "jarvisjarvis", "letmein12345", "adminadmin12", "iloveyou1234",
}


class BackupError(RuntimeError):
    code = "BACKUP_ERROR"


class PassphraseRejected(BackupError):
    code = "PASSPHRASE_REJECTED"


class KdfParamsRejected(BackupError):
    code = "KDF_PARAMS_REJECTED"


class ArchiveRejected(BackupError):
    code = "ARCHIVE_REJECTED"


class KeywrapMissing(BackupError):
    code = "KEYWRAP_MISSING"


class UnwrapFailed(BackupError):
    code = "KEY_UNWRAP_FAILED"


class RestoreRejected(BackupError):
    code = "RESTORE_REJECTED"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_passphrase(text):
    value = (text or "").strip()
    if len(value) < MIN_PASSPHRASE:
        raise PassphraseRejected("passphrase shorter than %d characters" % MIN_PASSPHRASE)
    if value.lower() in COMMON_PASSPHRASES:
        raise PassphraseRejected("passphrase appears in the common list")
    if len(set(value)) < 4:
        raise PassphraseRejected("passphrase has too little variety")
    return value


def new_recovery_code():
    raw = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def normalize_code(text):
    cleaned = "".join(ch for ch in (text or "").upper() if ch.isalnum())
    if len(cleaned) != 32:
        raise UnwrapFailed("recovery code has an unexpected length")
    return cleaned


def _derive(secret, salt, kdf):
    return low_level.hash_secret_raw(
        secret=secret, salt=salt,
        time_cost=int(kdf["t"]), memory_cost=int(kdf["m"]),
        parallelism=int(kdf["p"]), hash_len=32, type=low_level.Type.ID,
    )


def _wrap(bk, secret, kind):
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(_derive(secret, salt, ARGON2)).encrypt(nonce, bk, b"jarvis-backup-key-v1")
    return {
        "type": kind,
        "kdf": dict(ARGON2),
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "ciphertext": ciphertext.hex(),
    }


def _unwrap(entry, secret):
    kdf = entry.get("kdf") or {}
    if kdf.get("name") != "argon2id":
        raise KdfParamsRejected("unsupported KDF")
    for name in ("m", "t", "p"):
        try:
            value = int(kdf.get(name, 0))
        except (TypeError, ValueError):
            raise KdfParamsRejected("invalid KDF parameters")
        if value < ARGON2[name]:
            raise KdfParamsRejected("KDF parameters below the accepted floor")
    try:
        salt = bytes.fromhex(entry["salt"])
        nonce = bytes.fromhex(entry["nonce"])
        ciphertext = bytes.fromhex(entry["ciphertext"])
    except (KeyError, TypeError, ValueError) as exc:
        raise UnwrapFailed("malformed key wrap") from exc
    try:
        return AESGCM(_derive(secret, salt, kdf)).decrypt(nonce, ciphertext, b"jarvis-backup-key-v1")
    except Exception as exc:  # noqa: BLE001 - any AEAD failure means wrong secret
        raise UnwrapFailed("key wrap could not be opened") from exc


def _manifest_hmac(manifest, key):
    """HMAC over the manifest; ``key`` is the independent manifest key (or the
    legacy derived key for archives written before D4)."""
    body = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, body, hashlib.sha256).hexdigest()


def _legacy_manifest_key(bk):
    return hmac.new(bk, b"jarvis-backup-manifest-v1", hashlib.sha256).digest()


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot(source, target_path, bk):
    with apswmc_storage.connect(target_path, bk, create=True) as destination:
        with destination.raw.backup("main", source.raw, "main") as backup:
            while not backup.step(128):
                pass


def create_backup(vault_root, destination_dir, *, passphrase, recovery_code=None):
    """Write a self-contained archive and return its metadata (code only if generated)."""
    secret = validate_passphrase(passphrase)
    code = recovery_code or new_recovery_code()
    normalized = normalize_code(code)

    destination = Path(destination_dir)
    destination.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".jarvis-backup-staging-", dir=destination))
    try:
        bk = secrets.token_bytes(32)
        backup_key_id = secrets.token_hex(8)
        with vault_keys.open_vault(vault_root) as source:
            _snapshot(source, staging / DB, bk)
            tables = source.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            schema_version = source.execute("PRAGMA user_version").fetchone()[0]
        if apswmc_storage.plaintext_header(staging / DB):
            raise BackupError("backup database is not encrypted")

        wraps = [_wrap(bk, secret.encode("utf-8"), "passphrase"), _wrap(bk, normalized.encode("ascii"), "recovery_code")]
        # D4: the manifest key is independent from the backup key, so a database
        # key alone can never be used to forge a manifest.
        manifest_key = secrets.token_bytes(32)
        manifest_wraps = [
            _wrap(manifest_key, secret.encode("utf-8"), "passphrase"),
            _wrap(manifest_key, normalized.encode("ascii"), "recovery_code"),
        ]
        keywrap = {
            "format": KEYWRAP_FORMAT,
            "backup_key_id": backup_key_id,
            "wraps": wraps,
            "manifest_wraps": manifest_wraps,
        }
        (staging / KEYWRAP).write_text(json.dumps(keywrap, indent=2), encoding="utf-8")

        source_key_id = vault_keys.vault_status(vault_root).get("key_id")
        manifest = {
            "format": FORMAT,
            "created_at": _now(),
            "source_key_id": source_key_id,
            "backup_key_id": backup_key_id,
            "schema_version": int(schema_version),
            "tables": int(tables),
            "db_sha256": _sha256_file(staging / DB),
            "key_wraps": [
                {"type": entry["type"], "kdf": entry["kdf"], "salt": entry["salt"], "nonce": entry["nonce"]}
                for entry in wraps
            ],
            "manifest_wraps": [entry["type"] for entry in manifest_wraps],
            "hmac_mode": "independent",
        }
        manifest["manifest_hmac"] = _manifest_hmac(manifest, manifest_key)
        (staging / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        # Two backups within the same second would otherwise collide; keep the
        # readable timestamp name and add a counter only when needed.
        base = "jarvis-backup-%s-%s" % (_now().replace(":", "").replace("-", ""), source_key_id or "unknown")
        archive = destination / (base + SUFFIX)
        counter = 2
        while archive.exists():
            archive = destination / ("%s-%d%s" % (base, counter, SUFFIX))
            counter += 1
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for entry in (MANIFEST, DB, KEYWRAP):
                bundle.write(staging / entry, entry)
        info = {
            "archive": str(archive),
            "source_key_id": source_key_id,
            "backup_key_id": backup_key_id,
            "archive_sha256": _sha256_file(archive),
            "recovery_code": None if recovery_code else code,
        }
        return info
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def inspect_archive(path):
    """Report entries and any entry that must never travel with a backup."""
    with zipfile.ZipFile(path) as bundle:
        names = bundle.namelist()
    suspicious = [name for name in names if any(token in name.lower() for token in FORBIDDEN)]
    return {"entries": names, "unexpected": [n for n in names if n not in ALLOWED], "suspicious": suspicious}


def assert_upload_safe(path):
    report = inspect_archive(path)
    if report["suspicious"] or report["unexpected"]:
        raise ArchiveRejected("archive contains entries that must not be uploaded")
    return report


def _read_archive(path):
    with zipfile.ZipFile(path) as bundle:
        names = set(bundle.namelist())
        if names != ALLOWED:
            raise ArchiveRejected("archive entries are not exactly the expected set")
        manifest = json.loads(bundle.read(MANIFEST).decode("utf-8"))
        keywrap = json.loads(bundle.read(KEYWRAP).decode("utf-8"))
        database = bundle.read(DB)
    if manifest.get("format") != FORMAT:
        raise ArchiveRejected("unknown backup format")
    if keywrap.get("format") != KEYWRAP_FORMAT:
        raise ArchiveRejected("unknown key wrap format")
    digest = hashlib.sha256(database).hexdigest()
    if digest != manifest.get("db_sha256"):
        raise ArchiveRejected("database hash does not match the manifest")
    return manifest, keywrap, database


def restore_backup(archive, target_root, *, passphrase=None, recovery_code=None):
    """Restore into a new directory; never overwrites an existing vault."""
    manifest, keywrap, database = _read_archive(archive)
    target = Path(target_root)
    if os.path.lexists(target):
        raise RestoreRejected("restore target already exists")

    if passphrase is not None:
        secret, kind = validate_passphrase(passphrase).encode("utf-8"), "passphrase"
    elif recovery_code is not None:
        secret, kind = normalize_code(recovery_code).encode("ascii"), "recovery_code"
    else:
        raise RestoreRejected("a passphrase or a recovery code is required")

    entries = [entry for entry in keywrap.get("wraps", []) if entry.get("type") == kind]
    if not entries:
        raise KeywrapMissing("archive has no %s key wrap" % kind)
    bk = _unwrap(entries[0], secret)

    if manifest.get("backup_key_id") != keywrap.get("backup_key_id"):
        raise ArchiveRejected("key wrap and manifest disagree about the backup key")

    if manifest.get("hmac_mode") == "independent":
        manifest_entries = [entry for entry in keywrap.get("manifest_wraps", []) if entry.get("type") == kind]
        if not manifest_entries:
            raise KeywrapMissing("archive has no manifest key wrap for %s" % kind)
        manifest_key = _unwrap(manifest_entries[0], secret)
    else:  # archives written before D4 derive the manifest key from the backup key
        manifest_key = _legacy_manifest_key(bk)
    covered = {k: v for k, v in manifest.items() if k != "manifest_hmac"}
    if _manifest_hmac(covered, manifest_key) != manifest.get("manifest_hmac"):
        raise ArchiveRejected("manifest signature does not verify")

    parent = target.parent
    if not parent.is_dir():
        raise RestoreRejected("restore parent directory does not exist")
    staging = Path(tempfile.mkdtemp(prefix=".jarvis-restore-staging-", dir=parent))
    try:
        (staging / DB).write_bytes(database)
        with apswmc_storage.connect(staging / DB, bk, create=False) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RestoreRejected("restored database failed integrity_check")
            stored = dict(connection.execute("SELECT name, value FROM vault_meta").fetchall())
        if stored.get("format") != vault_keys.FORMAT:
            raise RestoreRejected("restored database has an unexpected vault format")
        if stored.get("key_id") != manifest.get("source_key_id"):
            raise RestoreRejected("restored key_id does not match the manifest")
        vault_keys.write_key_file(staging, bk)
        vault_keys.write_meta(staging, stored["key_id"])
        if os.path.lexists(target):
            raise RestoreRejected("restore target appeared during restore")
        os.replace(staging, target)
        return {"root": str(target), "key_id": stored["key_id"], "backup_key_id": manifest.get("backup_key_id")}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
