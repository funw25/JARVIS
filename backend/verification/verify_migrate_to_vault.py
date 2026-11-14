"""Verify G6 (plaintext to vault copy) and G7 (rollback drill), synthetic only."""
import json
import os
import sqlite3
from contextlib import closing
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".jarvis-python314-apswmc-probe"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(PROBE))

if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not PROBE.is_dir():
    print("MIGRATE_TO_VAULT=NOT_RUN (isolated Windows CPython 3.14 required)")
    raise SystemExit(2)
try:
    import apsw  # noqa: F401
except (ImportError, OSError):
    print("MIGRATE_TO_VAULT=NOT_RUN (candidate unavailable)")
    raise SystemExit(2)

os.environ["JARVIS_DATA_MODE"] = "synthetic"
# The source must be a plaintext database even when this suite runs with the
# experimental adapter selected, so clear those switches locally.
for name in ("JARVIS_STORAGE_ADAPTER", "JARVIS_APSWMC_KEY", "JARVIS_VAULT_DIR"):
    os.environ.pop(name, None)

from experimental import apswmc_storage, migrate_to_vault, vault_keys

TODOS = 3
STAMP = "2026-01-01T00:00:00Z"


def expect(error, call, label):
    try:
        call()
    except error:
        return
    except Exception as exc:  # noqa: BLE001
        raise AssertionError("%s raised %s instead of %s" % (label, type(exc).__name__, error.__name__)) from exc
    raise AssertionError("%s did not raise %s" % (label, error.__name__))


with tempfile.TemporaryDirectory(prefix="jarvis-migrate-") as directory:
    base = Path(directory)
    source_dir = base / "plaintext"
    source_dir.mkdir()
    os.environ["JARVIS_DATA_DIR"] = str(source_dir)
    import app.database as database
    database.apply_migrations()
    source_db = database.database_path()

    with closing(sqlite3.connect(source_db)) as connection, connection:
        for assistant in ("companion", "jarvis"):
            connection.execute(
                "INSERT OR IGNORE INTO assistant_profiles VALUES (?,?,?,?,?,?,?)",
                (assistant, assistant.title(), "assistant", 1, 1, STAMP, STAMP),
            )
        for index in range(TODOS):
            connection.execute(
                "INSERT INTO todos (id, owner_id, created_by_assistant, title, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                ("todo-%d" % index, "local", "jarvis", "synthetic %d" % index, STAMP, STAMP),
            )
        source_counts = {
            row[0]: connection.execute('SELECT count(*) FROM "%s"' % row[0]).fetchone()[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    vault = base / "vault"
    report = migrate_to_vault.migrate(source_db, vault)
    assert report["verified"] is True, report
    assert report["total_rows"] == sum(source_counts.values()), (report["total_rows"], sum(source_counts.values()))
    by_name = {item["name"]: item for item in report["tables"]}
    assert by_name["todos"]["rows"] == TODOS, by_name["todos"]
    assert (vault / "migration-report.json").is_file()
    print("G6_COPY_MATCHES_SOURCE_ROWS_AND_DIGESTS=PASS tables=%d rows=%d" % (len(by_name), report["total_rows"]))

    key = vault_keys._load_key(vault)
    cm = apswmc_storage.connect(vault / vault_keys.DBFILE, key, create=False)
    handle = cm.__enter__()
    try:
        assert handle.execute("SELECT count(*) FROM todos").fetchone()[0] == TODOS
        assert handle.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == len(list(database.MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")))
        assert handle.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        cm.__exit__(None, None, None)
    assert (vault / vault_keys.DBFILE).read_bytes()[:16] != b"SQLite format 3\x00"
    print("G6_VAULT_IS_ENCRYPTED_AND_FOREIGN_KEYS_CLEAN=PASS")

    expect(migrate_to_vault.MigrationError, lambda: migrate_to_vault.migrate(source_db, vault), "existing target")
    print("G6_REFUSES_TO_OVERWRITE_A_VAULT=PASS")

    # A source that carries a reserved table name must abort and leave no vault behind.
    broken = base / "broken.sqlite3"
    with closing(sqlite3.connect(broken)) as connection, connection:
        connection.execute("CREATE TABLE vault_meta(name TEXT)")
        connection.execute("INSERT INTO vault_meta VALUES ('x')")
    unsafe_target = base / "unsafe-vault"
    expect(migrate_to_vault.MigrationError, lambda: migrate_to_vault.migrate(broken, unsafe_target), "reserved table")
    assert not unsafe_target.exists(), "aborted migration left a vault behind"
    print("G6_ABORTS_ON_RESERVED_TABLE_WITHOUT_RESIDUE=PASS")

    # G7: restore the plaintext snapshot and prove it is usable.
    drill = migrate_to_vault.drill_rollback(source_db, base / "rollback")
    assert drill["integrity"] == "ok", drill
    assert drill["tables"]["todos"] == TODOS, drill["tables"].get("todos")
    assert drill["total_rows"] == sum(source_counts.values()), (drill["total_rows"], sum(source_counts.values()))
    restored = base / "rollback" / "jarvis-dev.sqlite3"
    with closing(sqlite3.connect(restored)) as connection, connection:
        assert connection.execute("SELECT count(*) FROM todos").fetchone()[0] == TODOS
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (base / "rollback" / "rollback-report.json").is_file()
    print("G7_ROLLBACK_RESTORES_PLAINTEXT_AND_IS_USABLE=PASS rows=%d" % drill["total_rows"])

    expect(migrate_to_vault.MigrationError, lambda: migrate_to_vault.drill_rollback(source_db, base / "rollback"), "existing rollback target")
    print("G7_REFUSES_TO_OVERWRITE_A_ROLLBACK_DIRECTORY=PASS")

print("MIGRATE_TO_VAULT_AND_ROLLBACK=PASS")
print("REAL_USER_DATA_AND_SWITCH=NOT_PERFORMED")
