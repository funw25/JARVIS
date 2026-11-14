"""Command line for the experimental vault backup job (synthetic data only).

Usage:
    python backup_cli.py store-passphrase          # prompt once, store in Credential Manager
    python backup_cli.py show-recovery-code        # print the code to write down offline
    python backup_cli.py run --vault DIR --local DIR [--remote] [--keep-local N] [--keep-remote N]

``--remote`` uses the R2_* variables from %USERPROFILE%\\.jarvis\\r2.env.
"""
import argparse
import getpass
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".jarvis-python314-apswmc-probe"))

from experimental import backup_job, backup_sink, vault_backup, vault_keys, wincred
from app.security.storage_policy import StorageSecurityError, require_synthetic_storage


def command_store_passphrase(args):
    secret = getpass.getpass("Backup passphrase (stored in Windows Credential Manager): ")
    if len(secret) < vault_backup.MIN_PASSPHRASE:
        raise SystemExit("passphrase is shorter than the %d character minimum" % vault_backup.MIN_PASSPHRASE)
    wincred.store_secret(secret)
    print("stored as %s" % backup_job.PASSPHRASE_TARGET)


def command_show_code(args):
    try:
        code = wincred.read_secret(backup_job.CODE_TARGET)
    except wincred.CredentialMissing:
        code, created = backup_job.resolve_recovery_code()
        print("generated a new recovery code " + ("(stored)" if created else ""))
    print(code)
    print("Write this down and keep it offline; it opens every backup without the passphrase.")


def _sink_for(args):
    if not getattr(args, "remote", False):
        return None
    credentials = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".jarvis" / "r2.env"
    return backup_sink.sink_from_environment(credentials)


def command_status(args):
    vault = Path(args.vault)
    status = vault_keys.rotation_status(vault, interval_days=args.interval_days)
    print("key_id:        %s" % status["key_id"])
    print("key since:     %s" % status["since"])
    print("age (days):    %s" % status["age_days"])
    print("rotation due:  %s (at %s, every %d days)" % (
        "YES" if status["due"] else "no", status["due_at"], status["interval_days"]))
    print("passphrase:    %s" % ("stored" if wincred.has_secret(backup_job.PASSPHRASE_TARGET) else "MISSING"))
    print("recovery code: %s" % ("stored" if wincred.has_secret(backup_job.CODE_TARGET) else "MISSING"))
    if args.local:
        archives = sorted(Path(args.local).glob("jarvis-backup-*.jvault"))
        print("local backups: %d (newest: %s)" % (
            len(archives), archives[-1].name if archives else "none"))
    if status["due"]:
        print("next: run 'backup_cli.py rotate --vault <vault> --local <dir>' (it backs up first)")


def command_rotate(args):
    vault = Path(args.vault)
    before = vault_keys.rotation_status(vault)
    print("current key_id: %s (age %s days)" % (before["key_id"], before["age_days"]))
    safety = backup_job.run(
        vault, local_dir=Path(args.local), sink=_sink_for(args),
        keep_local=args.keep_local, keep_remote=args.keep_remote,
    )
    print("safety backup:  %s" % safety["archive"])
    if safety["uploaded"]:
        print("uploaded:       %s" % safety["uploaded"])
    rotated = vault_keys.rotate_vault(vault)
    backup_job.audit(vault, {
        "event": "key_rotated",
        "old_key_id": rotated["old_key_id"],
        "new_key_id": rotated["new_key_id"],
        "safety_archive": Path(safety["archive"]).name,
    })
    after = vault_keys.rotation_status(vault)
    print("new key_id:     %s" % rotated["new_key_id"])
    print("next rotation:  %s" % after["due_at"])


def command_run(args):
    vault = Path(args.vault)
    local = Path(args.local)
    sink = _sink_for(args)
    result = backup_job.run(
        vault, local_dir=local, sink=sink,
        keep_local=args.keep_local, keep_remote=args.keep_remote,
    )
    print("archive: %s" % result["archive"])
    print("key_id: %s  backup_key_id: %s  bytes: %d" % (result["key_id"], result["backup_key_id"], result["bytes"]))
    print("local pruned: %d" % len(result["local_pruned"]))
    if result["uploaded"]:
        print("uploaded: %s" % result["uploaded"])
        print("remote pruned: %d  locked: %d" % (len(result["remote_pruned"]), len(result["remote_locked"])))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Jarvis experimental vault backup")
    sub = parser.add_subparsers(dest="command", required=True)

    store = sub.add_parser("store-passphrase", help="store the passphrase in Windows Credential Manager")
    store.set_defaults(func=command_store_passphrase)

    show = sub.add_parser("show-recovery-code", help="print the recovery code for offline storage")
    show.set_defaults(func=command_show_code)

    run = sub.add_parser("run", help="create a backup, keep the local copy and optionally upload")
    run.add_argument("--vault", required=True, help="vault directory")
    run.add_argument("--local", required=True, help="local backup directory")
    run.add_argument("--remote", action="store_true", help="also upload to R2")
    run.add_argument("--keep-local", type=int, default=7)
    run.add_argument("--keep-remote", type=int, default=4)
    run.set_defaults(func=command_run)

    status = sub.add_parser("status", help="show rotation age, due date and stored credentials")
    status.add_argument("--vault", required=True, help="vault directory")
    status.add_argument("--local", help="local backup directory (shows the newest archive)")
    status.add_argument("--interval-days", type=int, default=vault_keys.ROTATION_INTERVAL_DAYS)
    status.set_defaults(func=command_status)

    rotate = sub.add_parser("rotate", help="back up first, then rotate the data key")
    rotate.add_argument("--vault", required=True, help="vault directory")
    rotate.add_argument("--local", required=True, help="local backup directory for the safety backup")
    rotate.add_argument("--remote", action="store_true", help="also upload the safety backup to R2")
    rotate.add_argument("--keep-local", type=int, default=7)
    rotate.add_argument("--keep-remote", type=int, default=4)
    rotate.set_defaults(func=command_rotate)

    arguments = parser.parse_args(argv)
    try:
        require_synthetic_storage()
        return arguments.func(arguments) or 0
    except StorageSecurityError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except backup_job.PassphraseUnavailable as exc:
        print("error: %s" % exc, file=sys.stderr)
        print("fix: python backend/experimental/backup_cli.py store-passphrase", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
