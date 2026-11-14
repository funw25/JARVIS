"""Exercise G1 release checks using fabricated evidence in a temporary directory."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.security.g1_release_gate import DRIVER_BINARY, REQUIRED_EVIDENCE, assess_g1


def digest(data):
    return hashlib.sha256(data).hexdigest()


with tempfile.TemporaryDirectory(prefix="jarvis-g1-gate-", ignore_cleanup_errors=True) as directory:
    root = Path(directory)
    (root / "docs").mkdir()
    probe = root / ".jarvis-python314-apswmc-probe"
    binary = probe / DRIVER_BINARY
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"fictional-driver-bytes")
    license_dir = probe / "fixture.dist-info" / "licenses"
    license_dir.mkdir(parents=True)
    license_file = license_dir / "LICENSES"
    license_file.write_text("Roger Binns; SQLite3 Multiple Ciphers; SQLite public domain", encoding="utf-8")
    evidence = {}
    for name in REQUIRED_EVIDENCE:
        target = root / "docs" / (name + ".txt")
        target.write_text("fictional " + name, encoding="utf-8")
        evidence[name] = {"path": "docs/" + name + ".txt", "sha256": digest(target.read_bytes())}
    binary_hash = digest(binary.read_bytes())
    manifest = {
        "wheel": {"dist_info": "fixture.dist-info"},
        "files": {DRIVER_BINARY: binary_hash,
                  "fixture.dist-info/licenses/LICENSES": digest(license_file.read_bytes())}, "known_gaps": [],
        "release_g1": {"state": "approved", "builder": "fixture-builder", "second_builder": "fixture-builder-two", "reviewer": "fixture-reviewer",
                       "reviewed_at": "2026-01-01T00:00:00Z", "build_first_sha256": binary_hash,
                       "build_second_sha256": binary_hash, "evidence": evidence},
    }
    manifest_path = root / "docs" / "driver-manifest.json"
    def write(value):
        manifest_path.write_text(json.dumps(value), encoding="utf-8")
    write(manifest)
    assert assess_g1(root) == [], assess_g1(root)  # fabricated fixture only, not project approval

    for name in REQUIRED_EVIDENCE:
        missing = copy.deepcopy(manifest)
        del missing["release_g1"]["evidence"][name]
        write(missing)
        assert assess_g1(root), "missing evidence was accepted: " + name
    missing_approval = copy.deepcopy(manifest)
    del missing_approval["release_g1"]
    write(missing_approval)
    assert assess_g1(root)
    same_reviewer = copy.deepcopy(manifest)
    same_reviewer["release_g1"]["reviewer"] = same_reviewer["release_g1"]["builder"]
    write(same_reviewer)
    assert assess_g1(root)
    same_builders = copy.deepcopy(manifest)
    same_builders["release_g1"]["second_builder"] = same_builders["release_g1"]["builder"]
    write(same_builders)
    assert assess_g1(root)
    traversal = copy.deepcopy(manifest)
    traversal["release_g1"]["evidence"]["sbom"]["path"] = "../outside"
    write(traversal)
    assert assess_g1(root)
    changed_build = copy.deepcopy(manifest)
    changed_build["release_g1"]["build_second_sha256"] = "0" * 64
    write(changed_build)
    assert assess_g1(root)
    write(manifest)
    binary.write_bytes(b"changed driver")
    assert assess_g1(root)
    binary.write_bytes(b"fictional-driver-bytes")
    extra = probe / "apsw" / "unexpected.py"
    extra.write_text("fictional extra file", encoding="utf-8")
    assert assess_g1(root)
    extra.unlink()
    license_file.write_text("incomplete", encoding="utf-8")
    assert assess_g1(root)
    license_file.write_text("Roger Binns; SQLite3 Multiple Ciphers; SQLite public domain", encoding="utf-8")
    (root / "docs" / "sbom.txt").write_text("changed evidence", encoding="utf-8")
    assert assess_g1(root)
    print("G1_RELEASE_EVIDENCE_FAILS_CLOSED=PASS")

release = subprocess.run([sys.executable, str(ROOT / "tools" / "release" / "check_release.py")], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
assert release.returncode != 0 and "RELEASE_BLOCKED=" in release.stdout, (release.returncode, release.stdout, release.stderr)
print("PROJECT_RELEASE_ENTRY_FAILS_CLOSED=PASS")

print("G1_RELEASE_GATE_TEST=PASS (fixture is not a real approval)")
