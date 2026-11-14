"""Verify the scheduled backup job: credentials, retention, audit and restore.

Uses temporary test credentials in Windows Credential Manager and deletes them
afterwards; no network access is required (the sink is a local directory).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".jarvis-python314-apswmc-probe"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(PROBE))

if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not PROBE.is_dir():
    print("VAULT_BACKUP_JOB=NOT_RUN (isolated Windows CPython 3.14 required)")
    raise SystemExit(2)
try:
    import apsw  # noqa: F401
except (ImportError, OSError):
    print("VAULT_BACKUP_JOB=NOT_RUN (candidate unavailable)")
    raise SystemExit(2)

os.environ["JARVIS_DATA_MODE"] = "synthetic"
os.environ.pop("JARVIS_BACKUP_PASSPHRASE", None)
os.environ.pop("JARVIS_BACKUP_RECOVERY_CODE", None)

from experimental import backup_job, backup_sink, vault_backup, vault_keys, wincred

RUN_ID = os.urandom(16).hex()
PASS_TARGET = "Jarvis:test-backup-passphrase-" + RUN_ID
CODE_TARGET = "Jarvis:test-backup-recovery-code-" + RUN_ID
PASSPHRASE = "scheduled backup passphrase 2026"
MARKER = "FICTIONAL-JOB-" + os.urandom(6).hex()

try:
    with tempfile.TemporaryDirectory(prefix="jarvis-backup-job-") as directory:
        base = Path(directory)
        # Reject unsafe retention before touching credentials, files, or a sink.
        from unittest.mock import patch
        with patch.object(backup_job, "resolve_passphrase", side_effect=AssertionError("credential access")):
            for local_keep, remote_keep in ((0, 4), (-1, 4), (1, 0), (1, -1), (True, 4), (1, 1.5)):
                try:
                    backup_job.run(base / "missing", local_dir=base / "not-created",
                                   keep_local=local_keep, keep_remote=remote_keep)
                except ValueError:
                    pass
                else:
                    raise AssertionError("unsafe retention accepted")
        assert not (base / "not-created").exists()
        for invalid in (-1, True, 1.5):
            try:
                backup_job.prune_local(base / "not-created", invalid)
            except ValueError:
                pass
            else:
                raise AssertionError("unsafe direct prune count accepted")
        print("JOB_REJECTS_UNSAFE_RETENTION_BEFORE_SIDE_EFFECTS=PASS")
        vault = base / "vault"
        vault_keys.create_vault(vault)
        with vault_keys.open_vault(vault) as connection:
            connection.execute("CREATE TABLE notes(value TEXT NOT NULL)")
            connection.execute("INSERT INTO notes VALUES (?)", (MARKER,))

        local = base / "local"
        remote = base / "remote"

        # Without any stored credential the job must refuse, not guess.
        try:
            backup_job.run(vault, local_dir=local, passphrase=None,
                           passphrase_target=PASS_TARGET, code_target=CODE_TARGET)
            raise AssertionError("job ran without a passphrase")
        except backup_job.PassphraseUnavailable:
            pass
        print("JOB_REQUIRES_A_PASSPHRASE_SOURCE=PASS")

        wincred.store_secret(PASSPHRASE, PASS_TARGET)
        assert wincred.read_secret(PASS_TARGET) == PASSPHRASE
        print("CREDENTIAL_MANAGER_ROUNDTRIP=PASS")

        sink = backup_sink.LocalDirSink(remote)
        first = backup_job.run(
            vault, local_dir=local, sink=sink, keep_local=1, keep_remote=1,
            passphrase_target=PASS_TARGET, code_target=CODE_TARGET,
            passphrase=wincred.read_secret(PASS_TARGET),
        )
        assert Path(first["archive"]).is_file()
        assert first["uploaded"], first
        code_created = first["code_created"]
        print("JOB_CREATES_AND_UPLOADS_ONE_ARCHIVE=PASS code_created=%s" % code_created)

        # The recovery code is reused on later runs so every archive stays openable.
        assert wincred.has_secret(CODE_TARGET), "job did not persist the recovery code"
        code = wincred.read_secret(CODE_TARGET)
        second = backup_job.run(
            vault, local_dir=local, sink=sink, keep_local=1, keep_remote=1,
            passphrase_target=PASS_TARGET, code_target=CODE_TARGET,
            passphrase=wincred.read_secret(PASS_TARGET),
        )
        assert second["code_created"] is False, "a new recovery code was generated on the second run"
        assert len(list(local.glob("jarvis-backup-*.jvault"))) == 1, "local retention kept too many archives"
        assert len(sink.list()) == 1, "remote retention kept too many archives"
        print("JOB_REUSES_CODE_AND_ENFORCES_RETENTION=PASS")

        # Both archives open with the stored passphrase and the stored code.
        newest = sorted(local.glob("jarvis-backup-*.jvault"), key=lambda p: p.stat().st_mtime)[-1]
        vault_backup.restore_backup(newest, base / "restored-pass", passphrase=PASSPHRASE)
        with vault_keys.open_vault(base / "restored-pass") as connection:
            assert connection.execute("SELECT value FROM notes").fetchone()[0] == MARKER
        vault_backup.restore_backup(newest, base / "restored-code", recovery_code=code)
        with vault_keys.open_vault(base / "restored-code") as connection:
            assert connection.execute("SELECT value FROM notes").fetchone()[0] == MARKER
        print("JOB_ARCHIVE_OPENS_WITH_BOTH_STORED_SECRETS=PASS")

        # The audit log records events without any secret material.
        audit_text = (vault / "audit.log").read_text(encoding="utf-8")
        lines = [json.loads(line) for line in audit_text.splitlines() if line.strip()]
        events = [line["event"] for line in lines]
        assert events.count("backup_created") == 2 and events.count("backup_uploaded") == 2, events
        assert PASSPHRASE not in audit_text and code not in audit_text, "secret leaked into the audit log"
        print("JOB_AUDIT_LOG_RECORDS_EVENTS_WITHOUT_SECRETS=PASS")

        # Retention tolerance: a locked remote object is reported, not fatal.
        class LockedSink(backup_sink.LocalDirSink):
            def delete(self, name):
                raise backup_sink.SinkLocked("synthetic lock")

        removed, locked = backup_job.prune_remote(LockedSink(remote), 0)
        assert not removed and locked, (removed, locked)
        print("JOB_TOLERATES_LOCKED_REMOTE_OBJECTS=PASS")
finally:
    for target in (PASS_TARGET, CODE_TARGET):
        wincred.delete_secret(target)

print("VAULT_BACKUP_JOB=PASS")
print("SCHEDULER_WIRING_AND_R2_UPLOAD_IN_JOB=NOT_IMPLEMENTED")
