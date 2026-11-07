"""Scheduled backup job: one archive, local copy, optional upload, retention, audit.

The passphrase and the recovery code are read from Windows Credential Manager
(``Jarvis:backup-passphrase`` and ``Jarvis:backup-recovery-code``) unless they
are supplied explicitly or through the environment. Nothing secret is logged.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import backup_sink, vault_backup, wincred

PASSPHRASE_TARGET = wincred.DEFAULT_TARGET
CODE_TARGET = wincred.TARGET_PREFIX + "backup-recovery-code"


class PassphraseUnavailable(RuntimeError):
    code = "PASSPHRASE_UNAVAILABLE"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_passphrase(explicit=None, target=PASSPHRASE_TARGET):
    if explicit:
        return explicit
    from_env = os.environ.get("JARVIS_BACKUP_PASSPHRASE")
    if from_env:
        return from_env
    try:
        return wincred.read_secret(target)
    except wincred.CredentialMissing as exc:
        raise PassphraseUnavailable(
            "no backup passphrase: store one with wincred.store_secret(...) or pass it explicitly"
        ) from exc


def resolve_recovery_code(explicit=None, target=CODE_TARGET):
    """Return (code, created) reusing the stored code so every archive is openable."""
    if explicit:
        return explicit, False
    from_env = os.environ.get("JARVIS_BACKUP_RECOVERY_CODE")
    if from_env:
        return from_env, False
    try:
        return wincred.read_secret(target), False
    except wincred.CredentialMissing:
        code = vault_backup.new_recovery_code()
        wincred.store_secret(code, target)
        return code, True


def audit(vault_root, event):
    """Append one JSON line; never pass secrets into this function."""
    path = Path(vault_root) / "audit.log"
    record = {"at": _now()}
    record.update(event)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def prune_local(directory, keep):
    archives = sorted(
        Path(directory).glob("jarvis-backup-*.jvault"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for old in archives[keep:]:
        old.unlink()
        removed.append(old.name)
    return removed


def prune_remote(sink, keep):
    removed, locked = [], []
    items = sorted(sink.list(), key=lambda item: item.get("modified") or item["key"], reverse=True)
    for item in items[keep:]:
        try:
            sink.delete(item["key"])
            removed.append(item["key"])
        except backup_sink.SinkLocked:
            locked.append(item["key"])  # retention lock wins; documented behaviour
    return removed, locked


def run(vault_root, *, local_dir, sink=None, passphrase=None, recovery_code=None,
        keep_local=7, keep_remote=4, passphrase_target=PASSPHRASE_TARGET, code_target=CODE_TARGET):
    """Create one backup, keep the local copy, optionally upload and prune."""
    secret = resolve_passphrase(passphrase, target=passphrase_target)
    code, code_created = resolve_recovery_code(recovery_code, target=code_target)
    made = vault_backup.create_backup(vault_root, local_dir, passphrase=secret, recovery_code=code)
    archive = Path(made["archive"])

    result = {
        "archive": str(archive),
        "key_id": made["source_key_id"],
        "backup_key_id": made["backup_key_id"],
        "bytes": archive.stat().st_size,
        "code_created": code_created,
        "local_pruned": prune_local(local_dir, keep_local),
        "uploaded": None,
        "remote_pruned": [],
        "remote_locked": [],
    }
    audit(vault_root, {
        "event": "backup_created",
        "archive": archive.name,
        "key_id": made["source_key_id"],
        "backup_key_id": made["backup_key_id"],
        "bytes": result["bytes"],
        "local_pruned": len(result["local_pruned"]),
    })

    if sink is not None:
        uploaded = sink.upload(archive)
        result["uploaded"] = uploaded["key"]
        result["remote_pruned"], result["remote_locked"] = prune_remote(sink, keep_remote)
        audit(vault_root, {
            "event": "backup_uploaded",
            "sink": uploaded["sink"],
            "key": uploaded["key"],
            "remote_pruned": len(result["remote_pruned"]),
            "remote_locked": len(result["remote_locked"]),
        })
    return result
