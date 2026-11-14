"""Dry run for S5: apply the real application schema inside an encrypted vault.

Runs the migrations through ``app.database`` in a child process, pointed at a
vault directory, and then confirms the vault still opens with its own key.
"""
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".jarvis-python314-apswmc-probe"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(PROBE))

if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not PROBE.is_dir():
    print("VAULT_APP_SCHEMA=NOT_RUN (isolated Windows CPython 3.14 required)")
    raise SystemExit(2)
try:
    import apsw  # noqa: F401
except (ImportError, OSError):
    print("VAULT_APP_SCHEMA=NOT_RUN (candidate unavailable)")
    raise SystemExit(2)

os.environ["JARVIS_DATA_MODE"] = "synthetic"

from experimental import vault_keys

BOOT = 'import sys; sys.path[:0] = [%r, %r]; ' % (str(ROOT / 'backend'), str(PROBE))
CHILD = BOOT + """
import json
import app.database as database
database.apply_migrations()
with database.connect() as connection:
    versions = connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    tables = connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    has_todos = connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='todos'").fetchone()[0]
print(json.dumps({"versions": versions, "tables": tables, "integrity": integrity, "todos": has_todos}))
"""

with tempfile.TemporaryDirectory(prefix="jarvis-vault-app-") as directory:
    vault = Path(directory) / "vault"
    info = vault_keys.create_vault(vault)
    key = vault_keys._load_key(vault)

    environment = dict(os.environ)
    environment.update({
        "JARVIS_DATA_MODE": "synthetic",
        "JARVIS_VAULT_DIR": str(vault),
        "JARVIS_STORAGE_ADAPTER": "apswmc",
        "JARVIS_APSWMC_KEY": key.hex(),
        "PYTHONPATH": str(ROOT / "backend") + os.pathsep + str(PROBE),
        "PYTHONIOENCODING": "utf-8",
    })
    run = subprocess.run(
        [sys.executable, "-c", CHILD], cwd=ROOT, env=environment,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    assert run.returncode == 0, run.stderr[-2000:]
    report = json.loads(run.stdout.strip().splitlines()[-1])
    assert report["integrity"] == "ok", report
    assert report["versions"] >= 17, report
    assert report["todos"] == 1, report
    print("APP_MIGRATIONS_RUN_INSIDE_VAULT=PASS versions=%d tables=%d" % (report["versions"], report["tables"]))

    # The vault wrapper and metadata survive the migrations.
    with vault_keys.open_vault(vault) as connection:
        stored = dict(connection.execute("SELECT name, value FROM vault_meta").fetchall())
    assert stored["key_id"] == info["key_id"], stored
    print("VAULT_META_AND_WRAPPER_SURVIVE_MIGRATIONS=PASS")

    ciphertext = (vault / vault_keys.DBFILE).read_bytes()
    assert ciphertext[:16] != b"SQLite format 3\x00", "vault database is plaintext"
    assert b"todos" not in ciphertext and b"schema_migrations" not in ciphertext
    print("APPLICATION_SCHEMA_NAMES_NOT_PLAINTEXT=PASS")

    # A backup of the vault with the application schema restores and keeps data.
    from experimental import vault_backup
    made = vault_backup.create_backup(vault, Path(directory) / "backups", passphrase="switch plan dry run passphrase")
    legacy = vault_backup.restore_backup(made["archive"], Path(directory) / "restored", passphrase="switch plan dry run passphrase")
    assert legacy["audit_status"] == "LEGACY_UNANCHORED"
    restored_root = Path(directory) / 'restored'
    restored_key = vault_keys._load_key(restored_root)
    check = subprocess.run(
        [sys.executable, "-c",
         BOOT + "import app.database as database\n"
         "with database.connect() as c:\n"
         "    print(c.execute('SELECT count(*) FROM schema_migrations').fetchone()[0])"],
        cwd=ROOT,
        env={**environment, "JARVIS_VAULT_DIR": str(restored_root), "JARVIS_APSWMC_KEY": restored_key.hex()},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    print("RESTORED_VAULT_REUSABLE=%s versions=%s" % (check.returncode == 0, check.stdout.strip()[-4:]))
    assert check.returncode == 0, check.stderr[-1500:]

    # The snapshot head belongs to the archive, not the later live vault.
    from usecases import audit_chain
    with vault_keys.open_vault(vault) as connection:
        connection.execute("BEGIN IMMEDIATE")
        first = audit_chain.append(connection, {"type": "fictional_first"})
    anchored = vault_backup.create_backup(vault, Path(directory) / "chain-backups", passphrase="switch plan dry run passphrase")
    with vault_keys.open_vault(vault) as connection:
        connection.execute("BEGIN IMMEDIATE")
        second = audit_chain.append(connection, {"type": "fictional_later"})
    assert second["seq"] == first["seq"] + 1
    old = vault_backup.restore_backup(anchored["archive"], Path(directory) / "chain-restored", passphrase="switch plan dry run passphrase")
    assert old["audit_status"] == "SNAPSHOT_INTERNALLY_CONSISTENT"
    with vault_keys.open_vault(Path(directory) / "chain-restored") as connection:
        assert audit_chain.verify(connection)["head"] == first["head"]
    # A whole older archive still appears internally consistent without a witness.
    assert first["head"] != second["head"]
    print("SNAPSHOT_AUDIT_HEAD_AND_OLD_ARCHIVE_LIMIT=PASS")

    # Even an authenticated but inconsistent manifest must not be restored.
    forged = Path(directory) / "inconsistent.jvault"
    with zipfile.ZipFile(anchored["archive"]) as src, zipfile.ZipFile(forged, "w") as dst:
        manifest = json.loads(src.read(vault_backup.MANIFEST))
        keywrap = json.loads(src.read(vault_backup.KEYWRAP))
        entry = next(item for item in keywrap["manifest_wraps"] if item["type"] == "passphrase")
        manifest_key = vault_backup._unwrap(entry, b"switch plan dry run passphrase")
        manifest["audit_snapshot"]["head"] = "0" * 64
        manifest["manifest_hmac"] = vault_backup._manifest_hmac({k: v for k, v in manifest.items() if k != "manifest_hmac"}, manifest_key)
        for name in src.namelist():
            dst.writestr(name, json.dumps(manifest).encode("utf-8") if name == vault_backup.MANIFEST else src.read(name))
    try:
        vault_backup.restore_backup(forged, Path(directory) / "inconsistent-restored", passphrase="switch plan dry run passphrase")
    except vault_backup.RestoreRejected:
        pass
    else:
        raise AssertionError("inconsistent authenticated audit head accepted")
    print("SNAPSHOT_AUDIT_HEAD_MISMATCH_REJECTED=PASS")

print("VAULT_APP_SCHEMA_DRYRUN=PASS")
print("PLAINTEXT_TO_VAULT_DATA_COPY_AND_ROLLBACK=NOT_IMPLEMENTED")
