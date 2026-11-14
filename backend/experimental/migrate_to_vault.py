"""S5/G6: copy a plaintext SQLite database into an encrypted vault, table by table.

The source is opened read-only and never modified. The vault is created fresh in
a staging directory, each table is copied with its original CREATE statement,
and every table is verified by row count and a content digest computed from both
sides. Any mismatch removes the staging vault and keeps the source untouched.

Synthetic data only; not wired into the application.
"""
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
import tempfile
import time
from pathlib import Path

from . import apswmc_storage, vault_keys

SKIP_PREFIXES = ("sqlite_",)
VAULT_TABLE = "vault_meta"


class MigrationError(RuntimeError):
    code = "MIGRATION_ERROR"


class VerificationFailed(MigrationError):
    code = "MIGRATION_VERIFICATION_FAILED"


def _quote(name):
    return '"' + str(name).replace('"', '""') + '"'


def _source_connection(path):
    target = Path(path).resolve()
    if not target.is_file():
        raise MigrationError("source database does not exist: %s" % target)
    connection = sqlite3.connect("file:%s?mode=ro" % target.as_posix(), uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _tables(connection):
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [(row["name"], row["sql"]) for row in rows]


def _columns(connection, table):
    return [(row["name"], int(row["pk"])) for row in connection.execute("PRAGMA table_info(%s)" % _quote(table))]


def _primary_key(columns):
    keys = sorted((pk, name) for name, pk in columns if pk > 0)
    return [name for _, name in keys]


def _digest(connection, table, primary_key):
    order = ", ".join(_quote(name) for name in primary_key) if primary_key else "rowid"
    statement = "SELECT * FROM %s ORDER BY %s" % (_quote(table), order)
    digest = hashlib.sha256()
    count = 0
    try:
        cursor = connection.execute(statement)
    except Exception as exc:  # noqa: BLE001 - WITHOUT ROWID tables have no rowid order
        if primary_key:
            raise
        cursor = connection.execute("SELECT * FROM %s" % _quote(table))
        digest.update(b"unordered")
        _ = exc
    for row in cursor:
        digest.update(json.dumps(list(row), default=str, ensure_ascii=False).encode("utf-8"))
        digest.update(b"\n")
        count += 1
    return digest.hexdigest(), count


def migrate(source_db, vault_root, *, verify_samples=3):
    """Copy every table into a new vault and verify the result."""
    started = time.monotonic()
    vault = Path(vault_root)
    if vault.exists():
        raise MigrationError("vault target already exists")
    source = _source_connection(source_db)
    try:
        staging = Path(tempfile.mkdtemp(prefix=".jarvis-migrate-staging-", dir=vault.parent))
    except BaseException:
        source.close()
        raise
    staged_vault = staging / vault.name
    try:
        tables = _tables(source)
        if not tables:
            raise MigrationError("source database has no tables")
        vault_keys.create_vault(staged_vault)
        key = vault_keys._load_key(staged_vault)

        expected = {}
        copied = []
        with apswmc_storage.connect(staged_vault / vault_keys.DBFILE, key, create=False) as handle:
            # Foreign keys are enforced again after the copy, once every table exists.
            handle.execute("PRAGMA foreign_keys=OFF")
            for name, statement in tables:
                if name == VAULT_TABLE:
                    raise MigrationError("source contains a reserved table name: " + VAULT_TABLE)
                if not statement:
                    raise MigrationError("table without a CREATE statement: " + name)
                handle.execute(statement)
                columns = _columns(source, name)
                if not columns:
                    raise MigrationError("table without columns: " + name)
                names = [column for column, _ in columns]
                insert = "INSERT INTO %s (%s) VALUES (%s)" % (
                    _quote(name), ", ".join(_quote(column) for column in names),
                    ", ".join("?" * len(names)),
                )
                rows = source.execute("SELECT %s FROM %s" % (
                    ", ".join(_quote(column) for column in names), _quote(name)))
                copied_rows = 0
                for row in rows:
                    handle.execute(insert, tuple(row))
                    copied_rows += 1
                expected[name] = {
                    "rows": copied_rows,
                    "digest": _digest(source, name, _primary_key(_columns(source, name)))[0],
                }
                copied.append({"name": name, "rows": copied_rows})
            names = {name for name, _ in tables}
            if names.intersection({"audit_identity", "audit_chain"}):
                if not {"audit_identity", "audit_chain"}.issubset(names):
                    raise VerificationFailed("source audit tables are incomplete")
                from usecases import audit_chain
                if audit_chain.verify(source)["status"] != "LOCAL_CHAIN_CONSISTENT_UNANCHORED":
                    raise VerificationFailed("source audit chain is invalid")
                identity = source.execute("SELECT vault_id FROM audit_identity WHERE singleton=1").fetchone()
                if identity:
                    handle.execute("UPDATE vault_meta SET value=? WHERE name='vault_id'", (identity[0],))
            handle.commit()

        # Reopen with foreign keys on and check both directions.
        with apswmc_storage.connect(staged_vault / vault_keys.DBFILE, key, create=False) as check:
            violations = check.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise VerificationFailed("foreign key violations after copy: %d" % len(violations))

        mismatches = []
        with apswmc_storage.connect(staged_vault / vault_keys.DBFILE, key, create=False) as reader:
            for name, _ in tables:
                digest, count = _digest(reader, name, _primary_key(_columns(source, name)))
                if count != expected[name]["rows"] or digest != expected[name]["digest"]:
                    mismatches.append({"table": name, "source_rows": expected[name]["rows"],
                                       "vault_rows": count, "digest_match": digest == expected[name]["digest"]})
        if mismatches:
            raise VerificationFailed("verification failed: %s" % json.dumps(mismatches))
        if (staged_vault / vault_keys.DBFILE).read_bytes()[:16] == b"SQLite format 3\x00":
            raise VerificationFailed("migrated vault is plaintext")
        if verify_samples:
            _verify_service_readable(staged_vault, tables[0][0])

        report = {
            "source": str(Path(source_db).resolve()),
            "vault": str(vault),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "duration_seconds": round(time.monotonic() - started, 3),
            "tables": [dict(item, digest=expected[item["name"]]["digest"]) for item in copied],
            "total_rows": sum(item["rows"] for item in copied),
            "verified": True,
        }
        if vault.exists():
            raise MigrationError("vault target appeared during migration")
        staged_vault.rename(vault)
        staging.rmdir() if staging.exists() else None
        report["vault"] = str(vault)
        report["report_path"] = str(_write_report(vault, report))
        return report
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        source.close()





def _verify_service_readable(vault, table):
    """Cross-check that the copied database still reads through the plain adapter."""
    key = vault_keys._load_key(vault)
    connection = apswmc_storage.connect(Path(vault) / vault_keys.DBFILE, key, create=False)
    handle = connection.__enter__()
    try:
        handle.execute("SELECT count(*) FROM %s" % _quote(table)).fetchone()
    finally:
        connection.__exit__(None, None, None)


def _write_report(vault, report):
    path = Path(vault) / "migration-report.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def drill_rollback(source_db, target_root):
    """G7: restore the plaintext snapshot into a fresh directory and prove it works."""
    started = time.monotonic()
    target = Path(target_root)
    if target.exists():
        raise MigrationError("rollback target already exists")
    source = Path(source_db).resolve()
    if not source.is_file():
        raise MigrationError("snapshot does not exist: %s" % source)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir()
    try:
        database = target / "jarvis-dev.sqlite3"
        shutil.copy2(source, database)
        with closing(sqlite3.connect(database)) as connection, connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise VerificationFailed("rollback integrity_check: " + integrity)
            counts = {}
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
                name = row[0]
                counts[name] = connection.execute("SELECT count(*) FROM %s" % _quote(name)).fetchone()[0]
        report = {
            "snapshot": str(source),
            "target": str(target),
            "duration_seconds": round(time.monotonic() - started, 3),
            "integrity": "ok",
            "tables": counts,
            "total_rows": sum(counts.values()),
        }
        (target / "rollback-report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        return report
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
