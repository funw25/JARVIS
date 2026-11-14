"""Report driver provenance and detect drift against docs/driver-manifest.json.

This is a fact-reporting check: it verifies the shipped binaries against the
recorded hashes and prints the gaps that keep gate G1 open (missing license text,
unidentifiable crypto backend). It never treats the experimental build as
approved for real data.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / ".jarvis-python314-apswmc-probe"
MANIFEST = ROOT / "docs" / "driver-manifest.json"
sys.path.insert(0, str(PROBE))
sys.path.insert(0, str(ROOT / "backend"))
from app.security.g1_release_gate import assess_g1  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--release", action="store_true", help="fail if the full G1 release evidence is not approved")
args = parser.parse_args()

if sys.platform != "win32" or sys.version_info[:2] != (3, 14) or not PROBE.is_dir():
    print("DRIVER_PROVENANCE=NOT_RUN (isolated Windows CPython 3.14 required)")
    raise SystemExit(2)
if not MANIFEST.is_file():
    print("DRIVER_PROVENANCE=NOT_RUN (docs/driver-manifest.json missing)")
    raise SystemExit(2)
try:
    import apsw
except (ImportError, OSError):
    print("DRIVER_PROVENANCE=NOT_RUN (candidate unavailable)")
    raise SystemExit(2)

recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))
runtime = recorded.get("runtime", {})

actual = {
    "apsw": apsw.apswversion(),
    "sqlite": apsw.sqlitelibversion(),
    "sqlite3mc": getattr(apsw, "mc_version", ""),
}
if actual != runtime:
    print("DRIVER_VERSIONS_MATCH_MANIFEST=FAIL")
    raise SystemExit(1)
print("DRIVER_VERSIONS_MATCH_MANIFEST=PASS %s" % json.dumps(actual))

mismatches = []
for relative, expected in recorded.get("files", {}).items():
    path = PROBE / relative
    if not path.is_file():
        mismatches.append({"file": relative, "problem": "missing"})
        continue
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        mismatches.append({"file": relative, "problem": "hash", "expected": expected[:16], "actual": digest[:16]})
if mismatches:
    print("DRIVER_FILE_HASHES_MATCH_MANIFEST=FAIL %s" % json.dumps(mismatches))
    raise SystemExit(1)
print("DRIVER_FILE_HASHES_MATCH_MANIFEST=PASS files=%d" % len(recorded.get("files", {})))

license_dir = PROBE / recorded["wheel"]["dist_info"] / "licenses"
license_files = sorted(license_dir.glob("*")) if license_dir.is_dir() else []
text = " ".join(path.read_text(encoding="utf-8", errors="replace") for path in license_files if path.is_file())
covered = []
if "Roger Binns" in text:
    covered.append("APSW")
if re.search(r"Multiple Ciphers", text):
    covered.append("SQLite3MC")
if re.search(r"public domain|SQLite", text, re.IGNORECASE):
    covered.append("SQLite")
print("DRIVER_LICENSE_TEXT=%s" % ("PRESENT " + ",".join(covered) if license_files else "MISSING"))

binary = (PROBE / "apsw" / "__init__.cp314-win_amd64.pyd").read_bytes()
markers = {
    "openssl": bool(re.search(rb"(?i)openssl", binary)),
    "botan": bool(re.search(rb"(?i)botan", binary)),
    "sqlcipher": bool(re.search(rb"(?i)sqlcipher", binary)),
}
identified = [name for name, present in markers.items() if present and name != "sqlcipher"]
print("DRIVER_CRYPTO_BACKEND=%s" % ("IDENTIFIED:" + ",".join(identified) if identified else "UNKNOWN"))

gaps = []
if not license_files:
    gaps.append("no license text in the wheel")
elif len(covered) < 3:
    gaps.append("license texts incomplete: " + ",".join(covered))
if not identified:
    gaps.append("crypto backend not identifiable from the binary")
release_gaps = assess_g1(ROOT)
print("G1_GATE=%s" % ("CLOSED" if not release_gaps else "OPEN (%s)" % "; ".join(release_gaps)))
if gaps:
    print("DRIVER_DIAGNOSTIC_WARNINGS=%s" % "; ".join(gaps))
print("REAL_DATA_APPROVAL=NOT_GRANTED")

print("DRIVER_PROVENANCE_DIAGNOSTIC=PASS (not release approval)")
if args.release and release_gaps:
    print("G1_RELEASE_CHECK=BLOCKED")
    raise SystemExit(1)
if args.release:
    print("G1_RELEASE_CHECK=PASS")
