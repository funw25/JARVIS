"""Check the pinned public source archive and wheel against installed candidate files.

No downloaded source is extracted or executed. This is input verification, not
proof that the wheel was built from the source or a G1 release approval.
"""
import csv
import hashlib
import io
import json
import re
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / "tools" / "driver" / "candidate-inputs.json"
DOWNLOADS = ROOT / ".jarvis-driver-g1" / "downloads"
PROBE = ROOT / ".jarvis-python314-apswmc-probe"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    paths = {}
    for kind in ("source", "wheel", "sqlite3mc_license"):
        entry = lock[kind]
        target = DOWNLOADS / entry["filename"]
        require(target.is_file() and not target.is_symlink(), kind + " archive missing or unsafe")
        raw = target.read_bytes()
        require(len(raw) == entry["size"] and sha256_bytes(raw) == entry["sha256"], kind + " archive mismatch")
        paths[kind] = target
        print(kind.upper() + "_ARCHIVE_HASH=PASS")

    source = lock["source"]
    with tarfile.open(paths["source"], "r:gz") as archive:
        member = archive.getmember(source["amalgamation_path"])
        require(member.isfile() and member.size < 32 * 1024 * 1024, "unsafe amalgamation member")
        data = archive.extractfile(member).read()
        require(sha256_bytes(data) == source["amalgamation_sha256"], "amalgamation hash mismatch")
        require(b"SQLITE3MC_VERSION_STRING" in data, "SQLite3MC marker missing from amalgamation")
        for name, version in (("SQLITE_VERSION", lock["source_sqlite_version"]),
                              ("SQLITE3MC_VERSION_STRING", lock["source_sqlite3mc_version"])):
            pattern = rb"#\s*define\s+" + name.encode() + rb'\s+"' + re.escape(version.encode()) + rb'"'
            require(re.search(pattern, data) is not None, name + " missing or unexpected")
    print("SOURCE_AMALGAMATION_HASH=PASS")

    record_name = lock["wheel"]["record"]
    matched = 0
    with zipfile.ZipFile(paths["wheel"]) as archive:
        for name in archive.namelist():
            if name.endswith("/") or name == record_name:
                continue
            path = PurePosixPath(name)
            require(not path.is_absolute() and ".." not in path.parts and "\\" not in name,
                    "unsafe wheel member")
            installed = PROBE.joinpath(*path.parts)
            require(installed.is_file() and not installed.is_symlink(), "installed file missing: " + name)
            require(sha256_bytes(installed.read_bytes()) == sha256_bytes(archive.read(name)),
                    "installed file differs from wheel: " + name)
            matched += 1
        original = {row[0]: row[1:] for row in csv.reader(io.StringIO(archive.read(record_name).decode("utf-8")))}
        installed_record = PROBE.joinpath(*PurePosixPath(record_name).parts)
        current = {row[0]: row[1:] for row in csv.reader(io.StringIO(installed_record.read_text(encoding="utf-8")))}
        require(all(current.get(name) == values for name, values in original.items()),
                "installed RECORD changed a wheel entry")
        extra = len(set(current) - set(original))
    print("WHEEL_INSTALLED_FILES_MATCH=PASS count=%d" % matched)
    print("INSTALLED_RECORD_PRESERVES_WHEEL_ENTRIES=PASS extra_entries=%d" % extra)
    print("G1_SOURCE_TO_WHEEL_BUILD=UNVERIFIED")
    print("G1_RELEASE_APPROVAL=NOT_GRANTED")


if __name__ == "__main__":
    main()
