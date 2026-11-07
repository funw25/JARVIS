import hashlib
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .settings import database_path
from .security.storage_policy import require_synthetic_storage

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class MigrationStateError(RuntimeError):
    """Migration files or recorded versions are inconsistent."""


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    require_synthetic_storage()
    db_path = path or database_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _statements(script: str) -> Iterator[str]:
    """Split only at complete SQLite statements, including quoted semicolons and triggers."""
    buffer = []
    for char in script:
        buffer.append(char)
        if char == ";":
            candidate = "".join(buffer)
            if sqlite3.complete_statement(candidate):
                yield candidate
                buffer = []
    tail = "".join(buffer).strip()
    if tail:
        yield tail


def _check_statement(statement: str) -> None:
    remaining = statement.lstrip()
    while remaining.startswith("--") or remaining.startswith("/*"):
        if remaining.startswith("--"):
            end = remaining.find("\n")
            remaining = "" if end < 0 else remaining[end + 1:].lstrip()
        else:
            end = remaining.find("*/")
            if end < 0:
                raise MigrationStateError("Unclosed migration comment")
            remaining = remaining[end + 2:].lstrip()
    match = re.match(r"([A-Za-z]+)", remaining)
    if match is None:
        return
    keyword = match.group(1).upper()
    if keyword in {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE", "VACUUM", "ATTACH", "DETACH"}:
        raise MigrationStateError(f"Migration statement is not allowed: {keyword}")
    if keyword == "PRAGMA":
        normalized = re.sub(r"\s+", "", remaining).lower().rstrip(";")
        if normalized not in {"pragmaforeign_keys=on", "pragmaforeign_keys=1"}:
            raise MigrationStateError("Only PRAGMA foreign_keys=ON is allowed in migrations")


def apply_migrations(path: Path | None = None, *, allow_legacy_baseline: bool = False) -> None:
    """Apply migrations atomically and reject changed historical migration files.

    A pre-checksum synthetic database requires an explicit, one-time baseline.
    Baselines record the current files and cannot prove historical file contents.
    """
    files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    versions = [int(file.name.split("_", 1)[0]) for file in files]
    if not files or versions != list(range(1, len(files) + 1)):
        raise MigrationStateError("Migration versions must be unique and consecutive from 1")
    # Use the same byte snapshot for hashing and execution; do not reread between them.
    snapshots = {version: (file, file.read_bytes()) for version, file in zip(versions, files)}
    with connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL, sha256 TEXT NOT NULL)")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(schema_migrations)")}
        existing = conn.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()
        applied = [row["version"] for row in existing]
        if applied != versions[:len(applied)] or any(row["name"] != snapshots[row["version"]][0].name for row in existing):
            raise MigrationStateError("Recorded migration history does not match available files")
        if "sha256" not in columns:
            if existing and not allow_legacy_baseline:
                raise MigrationStateError("Legacy migration history has no checksums; explicit synthetic baseline required")
            conn.execute("ALTER TABLE schema_migrations ADD COLUMN sha256 TEXT")
            for row in existing:
                digest = hashlib.sha256(snapshots[row["version"]][1]).hexdigest()
                conn.execute("UPDATE schema_migrations SET sha256=? WHERE version=?", (digest, row["version"]))
        recorded = conn.execute("SELECT version,sha256 FROM schema_migrations ORDER BY version").fetchall()
        for row in recorded:
            actual = hashlib.sha256(snapshots[row["version"]][1]).hexdigest()
            if row["sha256"] != actual:
                raise MigrationStateError(f"Migration content does not match recorded checksum: {row['version']}")
        for version in versions:
            if version in applied:
                continue
            migration, raw = snapshots[version]
            for statement in _statements(raw.decode("utf-8")):
                _check_statement(statement)
                conn.execute(statement)
            digest = hashlib.sha256(raw).hexdigest()
            conn.execute("INSERT INTO schema_migrations(version, name, applied_at, sha256) VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'), ?)", (version, migration.name, digest))
