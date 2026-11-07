"""Backend-agnostic access to the application database for verification scripts.

Scripts must not open the database file with ``sqlite3`` directly: under the
experimental encrypted adapter that file is not a SQLite database. Use these
helpers so a script runs unchanged on both backends. ``sqlite3`` is still used
here only to build the exception tuples callers catch.
"""
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

from app import database as app_database

# The driver lives in the isolated probe directory; make it importable before the
# error tuples are built below. Only the matching interpreter may load it: the
# cp314 build imports as an empty stub under other versions.
_PROBE = Path(__file__).resolve().parents[2] / ".jarvis-python314-apswmc-probe"
if sys.version_info[:2] == (3, 14) and sys.platform == "win32" and _PROBE.is_dir() and str(_PROBE) not in sys.path:
    sys.path.insert(0, str(_PROBE))

try:  # only installed in the isolated 3.14 probe environment
    import apsw
except (ImportError, OSError):  # pragma: no cover - depends on the host
    apsw = None

if apsw is not None and not hasattr(apsw, "Error"):
    apsw = None  # wrong interpreter or a stub module: treat as unavailable

DATABASE_ERRORS = (sqlite3.DatabaseError,) + ((apsw.Error,) if apsw is not None else ())
CONSTRAINT_ERRORS = (sqlite3.IntegrityError,) + ((apsw.ConstraintError,) if apsw is not None else ())
OPERATIONAL_ERRORS = (sqlite3.OperationalError,) + ((apsw.SQLError,) if apsw is not None else ())


def adapter_active():
    return app_database.adapter_requested()


@contextmanager
def open_database(path=None):
    """Open the application database through the selected backend."""
    with app_database.connect(path) as connection:
        yield connection


def rows(statement, parameters=(), path=None):
    with open_database(path) as connection:
        return [tuple(row) for row in connection.execute(statement, parameters)]


def scalar(statement, parameters=(), path=None):
    result = rows(statement, parameters, path)
    return result[0][0] if result else None


def table_names(path=None):
    return {row[0] for row in rows("SELECT name FROM sqlite_master WHERE type='table'", (), path)}
