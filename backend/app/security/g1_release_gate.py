"""Fail-closed G1 release evidence checks for the selected encrypted driver.

This is an accidental-release control, not a cryptographic trust boundary against
someone who can edit the application or its repository. A passing evidence bundle
still needs independent human review and the other real-data gates.
"""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
REQUIRED_EVIDENCE = (
    "source_lock", "build_recipe", "build_log_first", "build_log_second", "sbom",
    "license_review", "dependency_review", "independent_review",
)
DRIVER_BINARY = "apsw/__init__.cp314-win_amd64.pyd"


def _safe_file(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        return None
    path = Path(relative)
    if path.is_absolute() or path.drive or any(part in (".", "..") for part in path.parts):
        return None
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            return None
    try:
        if not current.resolve().is_relative_to(root.resolve()) or not current.is_file():
            return None
    except OSError:
        return None
    return current


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assess_g1(root=ROOT):
    """Return all reasons G1 is not approved; missing or malformed evidence fails."""
    root = Path(root)
    manifest_path = _safe_file(root, "docs/driver-manifest.json")
    if manifest_path is None:
        return ["driver manifest missing or unsafe"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["driver manifest unreadable or invalid"]
    if not isinstance(manifest, dict):
        return ["driver manifest must be an object"]

    reasons = []
    if manifest.get("known_gaps") != []:
        reasons.append("driver known gaps are not explicitly cleared")
    files = manifest.get("files")
    if not isinstance(files, dict) or DRIVER_BINARY not in files:
        reasons.append("pinned driver files are missing")
        files = {}
    probe = root / ".jarvis-python314-apswmc-probe"
    if probe.is_symlink() or getattr(probe, "is_junction", lambda: False)():
        reasons.append("driver directory is unsafe")
        return reasons
    for name, digest in files.items():
        target = _safe_file(probe, name)
        try:
            valid = target is not None and isinstance(digest, str) and _sha256(target) == digest
        except OSError:
            valid = False
        if not valid:
            reasons.append("driver file missing, unsafe or changed: " + str(name))
    try:
        if not probe.is_dir():
            raise OSError("driver directory missing")
        unpinned = []
        for target in probe.rglob("*"):
            if target.is_symlink() or getattr(target, "is_junction", lambda: False)():
                reasons.append("driver directory contains a link")
                continue
            if target.is_file():
                name = target.relative_to(probe).as_posix()
                if name not in files:
                    unpinned.append(name)
        if unpinned:
            reasons.append("driver directory contains %d unpinned files" % len(unpinned))
    except OSError:
        reasons.append("driver directory is missing or unreadable")

    wheel = manifest.get("wheel")
    dist_info = wheel.get("dist_info") if isinstance(wheel, dict) else None
    licenses = []
    if isinstance(dist_info, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", dist_info):
        directory = probe / dist_info / "licenses"
        if directory.is_dir() and not directory.is_symlink() and not getattr(directory, "is_junction", lambda: False)():
            for path in directory.iterdir():
                safe = _safe_file(probe, dist_info + "/licenses/" + path.name)
                if safe is not None:
                    try:
                        licenses.append(safe.read_text(encoding="utf-8", errors="replace"))
                    except OSError:
                        pass
    license_text = " ".join(licenses)
    if not ("Roger Binns" in license_text and "Multiple Ciphers" in license_text
            and "SQLite" in license_text and "public domain" in license_text.lower()):
        reasons.append("installed driver license notices are incomplete or unreadable")

    review = manifest.get("release_g1")
    if not isinstance(review, dict) or review.get("state") != "approved":
        reasons.append("G1 independent approval is missing")
        return reasons
    people = (review.get("builder"), review.get("second_builder"), review.get("reviewer"))
    if (any(not isinstance(person, str) or not person.strip() for person in people)
            or len({person.strip().casefold() for person in people if isinstance(person, str)}) != 3):
        reasons.append("two independent builders and a separate reviewer must be identified")
    try:
        reviewed_at = datetime.fromisoformat(review.get("reviewed_at", "").replace("Z", "+00:00"))
        if reviewed_at.tzinfo is None:
            raise ValueError("timezone required")
    except (AttributeError, TypeError, ValueError):
        reasons.append("review timestamp missing or invalid")
    expected_binary = files.get(DRIVER_BINARY)
    if not isinstance(expected_binary, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_binary):
        reasons.append("driver binary digest is invalid")
    if review.get("build_first_sha256") != expected_binary or review.get("build_second_sha256") != expected_binary:
        reasons.append("two independent build digests do not match installed driver")

    evidence = review.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}
    for name in REQUIRED_EVIDENCE:
        entry = evidence.get(name)
        if not isinstance(entry, dict):
            reasons.append("missing G1 evidence: " + name)
            continue
        target = _safe_file(root, entry.get("path"))
        digest = entry.get("sha256")
        try:
            valid = (target is not None and target.stat().st_size > 0 and isinstance(digest, str)
                     and bool(re.fullmatch(r"[0-9a-f]{64}", digest)) and _sha256(target) == digest)
        except OSError:
            valid = False
        if not valid:
            reasons.append("invalid or changed G1 evidence: " + name)
    return reasons
