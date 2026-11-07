"""Experimental APSW SQLite3MC storage adapter (contract-first, synthetic data only).

This is not an application backend: there is no migration runner, no backup,
no recovery, no key lifecycle and no release review. It exists to prove that the
published storage contract can be satisfied by an encrypted driver before any
change to the default storage. Only temporary fictional data may pass through it.

Contract parity with ``app.database.connect`` is limited to the behaviour the
services rely on: a context manager that commits on success and rolls back on
error, ``BEGIN IMMEDIATE`` before the first write statement, rows addressable by
name and position with ``keys()``/``dict()``, ``rowcount`` for DML, ``lastrowid``,
cursor iteration/``fetchone``/``fetchall``/``fetchmany`` and close semantics.
"""
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from app.security.storage_policy import require_synthetic_storage

try:
    import apsw
except (ImportError, OSError):  # pragma: no cover - depends on the local probe
    apsw = None

WRITE_KEYWORDS = frozenset({"insert", "update", "delete", "replace", "create", "drop", "alter"})
_FIRST_WORD = re.compile(r"^\s*(?:--[^\n]*\n\s*)*([A-Za-z]+)")
_MISSING = object()


class AdapterUnavailable(RuntimeError):
    """The isolated encrypted driver is not usable on this host."""


class Row:
    """The ``sqlite3.Row`` subset the services use, backed by a tuple."""

    __slots__ = ("_names", "_values")

    def __init__(self, names, values):
        self._names = tuple(names)
        self._values = tuple(values)

    def keys(self):
        return list(self._names)

    def __getitem__(self, key):
        if isinstance(key, str):
            try:
                return self._values[self._names.index(key)]
            except ValueError as exc:
                raise IndexError("No item with that key") from exc
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __eq__(self, other):
        # sqlite3.Row compares by value with tuples and other rows; migrations and
        # services rely on that, so the experiment must behave the same way.
        if isinstance(other, Row):
            return self._names == other._names and self._values == other._values
        if isinstance(other, (tuple, list)):
            return self._values == tuple(other)
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    __hash__ = None

    def __repr__(self):
        return "<Row %r>" % dict(zip(self._names, self._values),)


def _first_keyword(statement):
    match = _FIRST_WORD.match(statement)
    return match.group(1).lower() if match else ""


def _driver():
    if apsw is None or "Multiple Ciphers" not in getattr(apsw, "mc_version", ""):
        raise AdapterUnavailable("Isolated SQLite3MC driver unavailable; no plaintext fallback")
    return apsw


class Cursor:
    """DB-API-shaped cursor over an APSW cursor, with the missing pieces added."""

    def __init__(self, connection, raw=None):
        self._connection = connection
        self._raw = raw
        self._names = ()
        self._rowcount = -1
        self._lastrowid = None
        if raw is not None:
            self._connection._register(self)

    # -- execution ---------------------------------------------------------
    def execute(self, statement, parameters=()):
        driver = self._connection.driver
        keyword = _first_keyword(statement)
        is_write = keyword in WRITE_KEYWORDS
        if is_write and not self._connection.in_transaction:
            self._connection.begin_immediate()
        self.close()
        self._raw = self._connection.raw.execute(statement, parameters)
        self._connection._register(self)
        try:
            description = self._raw.description
        except driver.ExecutionCompleteError:
            description = None
        self._names = tuple(column[0] for column in description) if description else ()
        if is_write:
            self._rowcount = self._connection.raw.changes()
            self._lastrowid = self._connection.raw.last_insert_rowid()
        else:
            self._rowcount = -1
            self._lastrowid = None
        return self

    def executemany(self, statement, sequences):
        driver = self._connection.driver
        keyword = _first_keyword(statement)
        if keyword in WRITE_KEYWORDS and not self._connection.in_transaction:
            self._connection.begin_immediate()
        total = 0
        lastrowid = None
        for parameters in sequences:
            self._raw = self._connection.raw.execute(statement, parameters)
            total += self._connection.raw.changes()
            lastrowid = self._connection.raw.last_insert_rowid()
        self._rowcount = total
        self._lastrowid = lastrowid
        self._names = ()
        return self

    # -- fetching ----------------------------------------------------------
    def fetchone(self):
        if self._raw is None:
            raise driver_error(self._connection, "no statement executed")
        values = self._raw.fetchone()
        if values is None:
            self.close()
            return None
        return Row(self._names, values)

    def fetchall(self):
        if self._raw is None:
            raise driver_error(self._connection, "no statement executed")
        rows = [Row(self._names, values) for values in self._raw.fetchall()]
        self.close()
        return rows

    def fetchmany(self, size=1):
        if self._raw is None:
            raise driver_error(self._connection, "no statement executed")
        rows = []
        for _ in range(size):
            values = self._raw.fetchone()
            if values is None:
                self.close()
                break
            rows.append(Row(self._names, values))
        return rows

    def __iter__(self):
        if self._raw is None:
            return iter(())
        return self._iterate()

    def _iterate(self):
        while True:
            values = self._raw.fetchone() if self._raw is not None else None
            if values is None:
                self.close()
                return
            yield Row(self._names, values)

    # -- DB-API metadata ---------------------------------------------------
    @property
    def description(self):
        return tuple((name, None, None, None, None, None, None) for name in self._names) or None

    @property
    def rowcount(self):
        return self._rowcount

    @property
    def lastrowid(self):
        return self._lastrowid

    def close(self):
        if self._raw is not None:
            try:
                self._raw.close()
            except Exception:
                pass
            self._raw = None
        self._connection._unregister(self)


def driver_error(connection, message):
    return AdapterUnavailable(message) if connection.driver is None else RuntimeError(message)


class Connection:
    """Context manager mirroring the sqlite3 adapter's transaction behaviour."""

    def __init__(self, raw, driver, key):
        self.raw = raw
        self.driver = driver
        self.key = key
        self._live = set()

    # -- statement bookkeeping --------------------------------------------
    def _register(self, cursor):
        self._live.add(cursor)

    def _unregister(self, cursor):
        self._live.discard(cursor)

    def _finalize_statements(self):
        """Release open statements first; APSW, unlike sqlite3, does not reset them."""
        for cursor in list(self._live):
            cursor.close()

    # -- transactions ------------------------------------------------------
    @property
    def in_transaction(self):
        return bool(self.raw.in_transaction)

    def begin_immediate(self):
        self.raw.execute("BEGIN IMMEDIATE")

    def commit(self):
        self._finalize_statements()
        if self.in_transaction:
            self.raw.execute("COMMIT")

    def rollback(self):
        self._finalize_statements()
        if self.in_transaction:
            self.raw.execute("ROLLBACK")

    # -- statements --------------------------------------------------------
    def execute(self, statement, parameters=()):
        return Cursor(self).execute(statement, parameters)

    def executemany(self, statement, sequences):
        return Cursor(self).executemany(statement, sequences)

    def cursor(self):
        return Cursor(self)

    def pragma(self, name, value=_MISSING):
        if value is _MISSING:
            return self.raw.pragma(name)
        return self.raw.pragma(name, value)

    def close(self):
        self._finalize_statements()
        if self.in_transaction:
            self.rollback()
        self.raw.close()


@contextmanager
def connect(path, key, *, create=True):
    """Open the experimental encrypted database; commit on success, roll back on error."""
    require_synthetic_storage()
    driver = _driver()
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("Experimental key must be 32 bytes")
    target = Path(path)
    if target.exists() and target.is_symlink():
        raise AdapterUnavailable("Refusing to open an experimental database through a symlink")
    flags = driver.SQLITE_OPEN_READWRITE | (driver.SQLITE_OPEN_CREATE if create else 0)
    if not create and not target.is_file():
        raise AdapterUnavailable("Experimental database is missing")
    try:
        # Same directory-creation contract as the sqlite3 implementation.
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise AdapterUnavailable("Experimental database directory is not creatable") from exc
    raw = driver.Connection(str(target), flags=flags)
    try:
        raw.pragma("busy_timeout", 5000)
        if raw.pragma("cipher", "chacha20") != "chacha20":
            raise AdapterUnavailable("Experimental cipher was not accepted")
        if raw.pragma("key", "raw:" + key.hex()) != "ok":
            raise AdapterUnavailable("Experimental key was not accepted")
        raw.pragma("temp_store", "MEMORY")
        raw.pragma("foreign_keys", 1)
        # WAL keeps readers from holding a shared lock across a writer's commit,
        # which is what the services' read-then-write patterns need. Switching the
        # journal mode needs a brief exclusive lock, so retry while another opener
        # is finishing its own switch.
        mode = None
        for attempt in range(5):
            try:
                mode = raw.pragma("journal_mode", "WAL")
            except driver.BusyError:
                mode = raw.pragma("journal_mode")
            if str(mode).lower() == "wal":
                break
            time.sleep(0.05 * (attempt + 1))
        if str(mode).lower() != "wal":
            raise AdapterUnavailable("Experimental WAL mode unavailable")
        # Reading the schema touches page 1, so a wrong key fails here rather than
        # on the caller's first statement. This build exposes its version only as
        # apsw.mc_version, not through PRAGMA cipher_version, so the cipher is
        # confirmed by the echo below and by the ciphertext checks in verification.
        list(raw.execute("SELECT name FROM sqlite_master"))
        if raw.pragma("cipher") != "chacha20":
            raise AdapterUnavailable("Experimental cipher is not active")
    except Exception:
        raw.close()
        raise
    connection = Connection(raw, driver, key)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def plaintext_header(path):
    """Return True when the file still starts with the standard SQLite header."""
    with open(path, "rb") as source:
        return source.read(16) == b"SQLite format 3\x00"
