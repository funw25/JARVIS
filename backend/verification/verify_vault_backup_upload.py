"""Verify B6/B7 against the real R2 bucket: guard, lock and remote restore.

Requires the isolated 3.14 probe, boto3 and the local credential file
``%USERPROFILE%\\.jarvis\\r2.env``. Writes one small synthetic backup under
``backups/selftest/`` so the bucket lock can be exercised; that object stays
until its retention period expires by design.
"""
import hashlib
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".jarvis-python314-apswmc-probe"
CREDENTIALS = Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".jarvis" / "r2.env"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(PROBE))

if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not PROBE.is_dir():
    print("VAULT_BACKUP_UPLOAD=NOT_RUN (isolated Windows CPython 3.14 required)")
    raise SystemExit(2)
if not CREDENTIALS.is_file():
    print("VAULT_BACKUP_UPLOAD=NOT_RUN (no R2 credential file)")
    raise SystemExit(2)
try:
    import boto3  # noqa: F401
    from botocore.exceptions import ClientError
except ImportError:
    print("VAULT_BACKUP_UPLOAD=NOT_RUN (boto3 not installed)")
    raise SystemExit(2)

os.environ["JARVIS_DATA_MODE"] = "synthetic"

from experimental import backup_sink, vault_backup, vault_keys

MARKER = "FICTIONAL-REMOTE-BACKUP-" + os.urandom(6).hex()
PASSPHRASE = "correct horse battery staple 2026"
TEST_PREFIX = "backups/selftest/"


def expect(error, call, label):
    try:
        call()
    except error:
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError("%s raised %s instead of %s" % (label, type(exc).__name__, error.__name__)) from exc
    raise AssertionError("%s did not raise %s" % (label, error.__name__))


def client_error(call):
    """Run an S3 call that should fail; return the error code, or None if it did not."""
    try:
        call()
    except ClientError as exc:
        return str(exc.response.get("Error", {}).get("Code", "")), str(exc.response.get("Error", {}).get("Message", ""))
    return None, ""


with tempfile.TemporaryDirectory(prefix="jarvis-vault-upload-", ignore_cleanup_errors=True) as directory:
    base = Path(directory)
    vault = base / "vault"
    vault_keys.create_vault(vault)
    with vault_keys.open_vault(vault) as connection:
        connection.execute("CREATE TABLE notes(value TEXT NOT NULL)")
        connection.execute("INSERT INTO notes VALUES (?)", (MARKER,))

    made = vault_backup.create_backup(vault, base / "local", passphrase=PASSPHRASE)
    archive = Path(made["archive"])
    remote_name = "remote-" + archive.name
    local_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    code = made["recovery_code"]

    sink = backup_sink.sink_from_environment(CREDENTIALS)
    sink.prefix = TEST_PREFIX
    print("SINK_CONFIGURED=PASS bucket=%s prefix=%s" % (sink.bucket, sink.prefix))

    # B6a: a guarded archive never leaves the machine.
    leaky = base / "leaky.jvault"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(leaky, "w", zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        target.writestr("recovery_code.txt", b"must never travel")
    expect(vault_backup.ArchiveRejected, lambda: sink.upload(leaky), "guarded upload")
    assert not any(item["key"].endswith("leaky.jvault") for item in sink.list()), "leaky archive reached the bucket"
    print("B6_UPLOAD_GUARD_REFUSES_RECOVERY_MATERIAL=PASS")

    # B6b: upload the real archive and confirm it is listed with the right size.
    uploaded = sink.upload(archive, name=remote_name)
    key = uploaded["key"]
    listing = sink.list()
    assert any(item["key"] == key and item["bytes"] == archive.stat().st_size for item in listing), listing
    print("B6_UPLOAD_AND_LISTING=PASS")

    # B6c: the sink refuses to overwrite an existing object.
    expect(backup_sink.SinkRejected, lambda: sink.upload(archive, name=remote_name), "no-overwrite")
    print("B6_NO_SILENT_OVERWRITE=PASS")

    # B6d: the bucket lock itself refuses overwrite and delete.
    overwrite_code, overwrite_message = client_error(
        lambda: sink.client.put_object(Bucket=sink.bucket, Key=key, Body=b"overwrite attempt")
    )
    delete_code, delete_message = client_error(lambda: sink.client.delete_object(Bucket=sink.bucket, Key=key))
    print("B6_LOCK_OVERWRITE=%s" % (overwrite_code or "ALLOWED"))
    print("B6_LOCK_DELETE=%s" % (delete_code or "ALLOWED"))
    if overwrite_code is None or delete_code is None:
        print("B6_LOCK_NOT_ENFORCED=FAIL (check the bucket lock rule on prefix 'backups/')")
        raise SystemExit(1)
    print("B6_BUCKET_LOCK_PREVENTS_OVERWRITE_AND_DELETE=PASS")

    # B7: download the remote copy and restore it, twice, with different secrets.
    downloaded = base / "downloaded.jvault"
    sink.download(key, downloaded)
    assert hashlib.sha256(downloaded.read_bytes()).hexdigest() == local_digest, "downloaded archive differs"
    vault_backup.restore_backup(downloaded, base / "restored-pass", passphrase=PASSPHRASE)
    with vault_keys.open_vault(base / "restored-pass") as connection:
        assert connection.execute("SELECT value FROM notes").fetchone()[0] == MARKER
    vault_backup.restore_backup(downloaded, base / "restored-code", recovery_code=code)
    with vault_keys.open_vault(base / "restored-code") as connection:
        assert connection.execute("SELECT value FROM notes").fetchone()[0] == MARKER
    print("B7_REMOTE_RESTORE_WITH_PASSPHRASE_AND_CODE=PASS")
    print("REMOTE_OBJECT=%s" % key)
    print("NOTE=the selftest object stays locked for its retention period")

print("VAULT_BACKUP_UPLOAD=PASS")
print("RETENTION_PRUNE_AND_SCHEDULING=NOT_IMPLEMENTED")
