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

os.environ.setdefault("JARVIS_DATA_MODE", "synthetic")

from experimental import backup_job, backup_sink, vault_backup, wincred


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


def command_run(args):
    vault = Path(args.vault)
    local = Path(args.local)
    sink = None
    if args.remote:
        credentials = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".jarvis" / "r2.env"
        sink = backup_sink.sink_from_environment(credentials)
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

    arguments = parser.parse_args(argv)
    try:
        return arguments.func(arguments) or 0
    except backup_job.PassphraseUnavailable as exc:
        print("error: %s" % exc, file=sys.stderr)
        print("fix: python backend/experimental/backup_cli.py store-passphrase", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
