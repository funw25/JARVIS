"""Probe an isolated APSW SQLite3MC wheel using only temporary synthetic data.

This is an experimental candidate, not an app integration or security audit.
"""
import os
import json
import secrets
import subprocess
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
candidate = ROOT / ".jarvis-python314-apswmc-probe"
if sys.version_info[:2] != (3, 14) or not candidate.is_dir():
    print("APSWMC_CANDIDATE=NOT_RUN (requires isolated Windows CPython 3.14 candidate)")
    raise SystemExit(2)
sys.path.insert(0, str(candidate))
try:
    import apsw
except (ImportError, OSError) as exc:
    print("APSWMC_CANDIDATE=NOT_RUN (isolated candidate unavailable: " + type(exc).__name__ + ")")
    raise SystemExit(2)
if not isinstance(getattr(apsw, "mc_version", None), str) or "Multiple Ciphers" not in apsw.mc_version:
    raise RuntimeError("Expected SQLite3 Multiple Ciphers; no plaintext fallback")


def open_db(path, key, *, create=False):
    flags = apsw.SQLITE_OPEN_READWRITE | (apsw.SQLITE_OPEN_CREATE if create else 0)
    conn = apsw.Connection(str(path), flags=flags)
    try:
        if conn.pragma("cipher", "chacha20") != "chacha20":
            raise RuntimeError("Explicit experimental cipher was not accepted")
        if conn.pragma("key", "raw:" + key.hex()) != "ok":
            raise RuntimeError("Experimental key was not accepted")
        list(conn.execute("SELECT name FROM sqlite_master"))
        return conn
    except Exception:
        conn.close()
        raise


if sys.argv[1:] == ["--restart-child"]:
    data = json.load(sys.stdin)
    with closing(open_db(Path(data["path"]), bytes.fromhex(data["key"]))) as conn:
        assert list(conn.execute("SELECT value FROM samples")) == [(data["marker"],)]
    print("ISOLATED_RESTART=PASS")
    raise SystemExit(0)

with tempfile.TemporaryDirectory(prefix="jarvis-apswmc-candidate-") as directory:
    path = Path(directory) / "synthetic.db"
    key = secrets.token_bytes(32)
    wrong = secrets.token_bytes(32)
    marker = "SYNTHETIC-ONLY-" + secrets.token_hex(24)
    with closing(open_db(path, key, create=True)) as conn:
        conn.pragma("temp_store", "MEMORY")
        assert conn.pragma("temp_store") == 2
        assert str(conn.pragma("journal_mode", "WAL")).lower() == "wal"
        conn.execute("CREATE TABLE samples(value TEXT NOT NULL)")
        conn.execute("INSERT INTO samples(value) VALUES (?)", (marker,))
        wal = Path(str(path) + "-wal")
        assert wal.is_file() and wal.stat().st_size > 0
        for item in (path, wal):
            assert marker.encode("ascii") not in item.read_bytes()
        assert path.read_bytes()[:16] != b"SQLite format 3\x00"
    with closing(open_db(path, key)) as conn:
        assert list(conn.execute("SELECT value FROM samples")) == [(marker,)]
    child = subprocess.run([sys.executable, __file__, "--restart-child"], input=json.dumps({"path": str(path), "key": key.hex(), "marker": marker}), text=True, capture_output=True, timeout=15)
    assert child.returncode == 0 and child.stdout.strip() == "ISOLATED_RESTART=PASS"
    try:
        conn = open_db(path, wrong)
    except apsw.Error:
        pass
    else:
        conn.close()
        raise AssertionError("Wrong key unexpectedly opened the database")
    try:
        with closing(sqlite3.connect(path)) as plain:
            plain.execute("SELECT value FROM samples").fetchall()
    except sqlite3.DatabaseError:
        pass
    else:
        raise AssertionError("Ordinary SQLite unexpectedly opened the database")
    # Modify an encrypted data page, not the header, and require an integrity failure.
    corrupt = Path(directory) / "corrupt.db"
    shutil.copyfile(path, corrupt)
    data = bytearray(corrupt.read_bytes())
    assert len(data) > 4200
    data[4200] ^= 0x01
    corrupt.write_bytes(data)
    try:
        conn = open_db(corrupt, key)
        try:
            list(conn.execute("SELECT value FROM samples"))
            list(conn.execute("PRAGMA integrity_check"))
        finally:
            conn.close()
    except apsw.Error:
        pass
    else:
        raise AssertionError("Corrupted synthetic database was not rejected")
    print("APSW_VERSION=" + apsw.apswversion())
    print("SQLITE_VERSION=" + apsw.sqlitelibversion())
    print("SQLITE3MC_VERSION=" + apsw.mc_version)
    print("CORRECT_KEY_WRONG_KEY_RESTART_AND_PLAIN_SQLITE=PASS")
    print("DATABASE_WAL_MARKER_AND_SINGLE_PAGE_TAMPER=PASS")
print("APSWMC_SYNTHETIC_CANDIDATE=PASS")
