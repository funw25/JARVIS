"""Verify the locked M1 environment: interpreter, ._pth, packages and wheel hashes.

Runs under the portable interpreter. Exits non-zero when any pinned fact drifted.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

if sys.version_info[:3] != (3, 14, 10):
    print("M1_ENV_LOCK=NOT_RUN (this check targets the pinned CPython 3.14.10 environment)")
    raise SystemExit(2)
LOCK = ROOT / "tools" / "env" / "requirements-m1.lock"
WHEELHOUSE = ROOT / ".jarvis-wheelhouse"
DEPS = ROOT / ".jarvis-python314-deps"
DEPS_ARG = None
for index, argument in enumerate(sys.argv):
    if argument == "--deps" and index + 1 < len(sys.argv):
        DEPS_ARG = Path(sys.argv[index + 1]).resolve()
DEPS = DEPS_ARG or (ROOT / ".jarvis-python314-deps")
EXPECTED_VERSION = "3.14.10"
EXPECTED_IMPL = "cpython"


def parse_lock(path):
    packages = {}
    for line in path.read_text(encoding="ascii").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9_.\-]+)==([^\s]+)\s+--hash=sha256:([0-9a-f]{64})", line)
        if not match:
            raise AssertionError("unparsable lock line: " + line)
        name, version, digest = match.groups()
        packages[name.replace("-", "_").lower()] = {"version": version, "hash": digest}
    return packages


def installed_version(name):
    import importlib.metadata as metadata

    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


lock = parse_lock(LOCK)
print("LOCK_PARSED=PASS packages=%d" % len(lock))

version = ".".join(str(part) for part in sys.version_info[:3])
assert version == EXPECTED_VERSION, "interpreter is %s, lock requires %s" % (version, EXPECTED_VERSION)
assert sys.implementation.name == EXPECTED_IMPL, sys.implementation.name
print("INTERPRETER_PINNED=PASS python=%s impl=%s" % (version, sys.implementation.name))

pth = Path(sys.prefix) / "python314._pth"
assert pth.is_file(), "python314._pth missing: %s" % pth
entries = [line.strip() for line in pth.read_text(encoding="ascii").splitlines() if line.strip() and not line.strip().startswith("#")]
custom = [entry for entry in entries if entry not in ("python314.zip", ".")]
assert custom, "._pth has no dependency entry: %s" % entries
if DEPS_ARG is None:
    DEPS = (Path(sys.prefix) / custom[-1]).resolve()
assert DEPS.is_dir(), "dependency directory missing: %s" % DEPS
import os

paths = {os.path.normcase(str(Path(entry).resolve())) for entry in sys.path if entry}
assert os.path.normcase(str(DEPS)) in paths, "dependency directory is not importable: %s" % DEPS
print("PTH_EXPOSES_DEPS=PASS deps=%s entries=%s" % (DEPS, entries))

missing, drifted = [], []
for name, item in sorted(lock.items()):
    actual = installed_version(name)
    if actual is None:
        missing.append(name)
    elif actual != item["version"]:
        drifted.append("%s expected %s got %s" % (name, item["version"], actual))
assert not missing, "missing packages: %s" % missing
assert not drifted, drifted
print("PACKAGES_MATCH_LOCK=PASS count=%d" % len(lock))

if WHEELHOUSE.is_dir():
    hashes = {item["hash"]: name for name, item in lock.items()}
    checked = 0
    problems = []
    for wheel in sorted(WHEELHOUSE.glob("*.whl")):
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        if digest in hashes:
            checked += 1
        else:
            problems.append(wheel.name)
    assert not problems, "wheelhouse contains wheels not in the lock: %s" % problems
    assert checked >= len(lock), "wheelhouse covers only %d of %d packages" % (checked, len(lock))
    print("WHEELHOUSE_MATCHES_LOCK=PASS wheels=%d" % checked)
else:
    print("WHEELHOUSE_MATCHES_LOCK=SKIPPED (no wheelhouse beside the repository)")

# The application imports these at runtime; a version-only check would miss a broken install.
import fastapi  # noqa: F401
import pydantic  # noqa: F401
import uvicorn  # noqa: F401
print("RUNTIME_IMPORTS=PASS fastapi=%s pydantic=%s" % (fastapi.__version__, pydantic.VERSION))

if len(sys.argv) > 1 and sys.argv[1] == "--json":
    print(json.dumps({"python": version, "packages": len(lock)}, ensure_ascii=False))
print("M1_ENV_LOCK=PASS")
