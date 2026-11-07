"""Verify migration atomicity, history checks, and concurrent startup on temporary data."""
import os
import sqlite3
import sys
import tempfile
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["JARVIS_DATA_MODE"] = "synthetic"
import app.database as database


def tables(path):
    with closing(sqlite3.connect(path)) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


with tempfile.TemporaryDirectory(prefix="jarvis-migration-check-") as directory:
    temp = Path(directory)
    migrations = temp / "migrations"
    migrations.mkdir()
    database.MIGRATIONS = migrations
    first = migrations / "0001_first.sql"
    second = migrations / "0002_second.sql"
    first.write_text("-- comment; this is not a statement\nCREATE TABLE first_table(value TEXT); INSERT INTO first_table VALUES ('value; inside');", encoding="utf-8")
    second.write_text("CREATE TABLE second_table(value TEXT); INSERT INTO missing_table VALUES ('error');", encoding="utf-8")
    fresh = temp / "fresh.sqlite3"
    try:
        database.apply_migrations(fresh)
        raise AssertionError("invalid migration unexpectedly succeeded")
    except sqlite3.OperationalError:
        pass
    assert "first_table" not in tables(fresh) and "schema_migrations" not in tables(fresh)
    print("FRESH_MIGRATION_FAILURE_ROLLS_BACK_ALL=PASS")

    second.unlink()
    existing = temp / "existing.sqlite3"
    database.apply_migrations(existing)
    second.write_text("CREATE TABLE second_table(value TEXT); INSERT INTO missing_table VALUES ('error');", encoding="utf-8")
    try:
        database.apply_migrations(existing)
        raise AssertionError("invalid upgrade unexpectedly succeeded")
    except sqlite3.OperationalError:
        pass
    with closing(sqlite3.connect(existing)) as conn:
        assert conn.execute("SELECT value FROM first_table").fetchone()[0] == "value; inside"
        assert conn.execute("SELECT version FROM schema_migrations").fetchall() == [(1,)]
    assert "second_table" not in tables(existing)
    print("UPGRADE_FAILURE_PRESERVES_PREVIOUS_VERSION=PASS")

    second.write_text("CREATE TABLE second_table(value TEXT); INSERT INTO second_table VALUES ('ok');", encoding="utf-8")
    database.apply_migrations(existing)
    database.apply_migrations(existing)
    with closing(sqlite3.connect(existing)) as conn:
        assert conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall() == [(1,), (2,)]
        assert conn.execute("SELECT value FROM second_table").fetchone()[0] == "ok"
    print("RETRY_AND_IDEMPOTENCE=PASS")

    concurrent = temp / "concurrent.sqlite3"
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(database.apply_migrations, [concurrent, concurrent]))
    with closing(sqlite3.connect(concurrent)) as conn:
        assert conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall() == [(1,), (2,)]
        assert conn.execute("SELECT COUNT(*) FROM first_table").fetchone()[0] == 1
    print("CONCURRENT_STARTUP_SERIALIZED=PASS")

    third = migrations / "0003_forbidden.sql"
    third.write_text("-- forbidden control statement\nCOMMIT;", encoding="utf-8")
    try:
        database.apply_migrations(existing)
        raise AssertionError("transaction control unexpectedly accepted")
    except database.MigrationStateError:
        pass
    with closing(sqlite3.connect(existing)) as conn:
        assert conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall() == [(1,), (2,)]
    third.unlink()
    second.rename(migrations / "0002_renamed.sql")
    try:
        database.apply_migrations(existing)
        raise AssertionError("mismatched history unexpectedly accepted")
    except database.MigrationStateError:
        pass
    print("UNSAFE_SQL_AND_HISTORY_MISMATCH_REJECTED=PASS")

    (migrations / "0002_renamed.sql").rename(second)
    original = first.read_bytes()
    first.write_bytes(original + b"\n-- changed after application\n")
    try:
        database.apply_migrations(existing)
        raise AssertionError("changed applied migration unexpectedly accepted")
    except database.MigrationStateError as exc:
        assert "checksum" in str(exc)
    first.write_bytes(original)
    database.apply_migrations(existing)
    print("CHANGED_HISTORY_REJECTED=PASS")

    legacy = temp / "legacy.sqlite3"
    with closing(sqlite3.connect(legacy)) as conn:
        conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE first_table(value TEXT)")
        conn.execute("INSERT INTO first_table VALUES ('legacy')")
        conn.execute("INSERT INTO schema_migrations VALUES (1,'0001_first.sql','old')")
        conn.commit()
    try:
        database.apply_migrations(legacy)
        raise AssertionError("legacy history silently baselined")
    except database.MigrationStateError as exc:
        assert "explicit synthetic baseline" in str(exc)
    with closing(sqlite3.connect(legacy)) as conn:
        assert "sha256" not in {row[1] for row in conn.execute("PRAGMA table_info(schema_migrations)")}
        assert conn.execute("SELECT value FROM first_table").fetchone() == ('legacy',)
    second.write_text("CREATE TABLE second_table(value TEXT); INSERT INTO missing_table VALUES ('error');", encoding="utf-8")
    try:
        database.apply_migrations(legacy, allow_legacy_baseline=True)
        raise AssertionError("invalid upgrade unexpectedly succeeded")
    except sqlite3.OperationalError:
        pass
    with closing(sqlite3.connect(legacy)) as conn:
        assert "sha256" not in {row[1] for row in conn.execute("PRAGMA table_info(schema_migrations)")}
        assert conn.execute("SELECT version FROM schema_migrations").fetchall() == [(1,)]
    second.write_text("CREATE TABLE second_table(value TEXT); INSERT INTO second_table VALUES ('ok');", encoding="utf-8")
    database.apply_migrations(legacy, allow_legacy_baseline=True)
    database.apply_migrations(legacy)
    with closing(sqlite3.connect(legacy)) as conn:
        assert conn.execute("SELECT count(*) FROM schema_migrations WHERE length(sha256)=64").fetchone()[0] == 2
        assert conn.execute("SELECT value FROM first_table").fetchone() == ('legacy',)
    print("EXPLICIT_LEGACY_BASELINE_AND_ATOMIC_UPGRADE=PASS")

print("MIGRATION_ATOMICITY_VERIFICATION=PASS")
