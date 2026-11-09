"""Verify the M1 labeled dataset: shape, coverage, verbatim evidence, duplicates."""
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

DATASET = ROOT / "backend" / "evals" / "m1" / "artifacts.jsonl"
CLASSES = {"needs_reply", "action", "fyi", "noise", "injection"}
PRIORITIES = {"low", "normal", "high"}
KINDS = {"email", "post", "note"}

rows = [json.loads(line) for line in DATASET.read_text(encoding="utf-8").splitlines() if line.strip()]
assert 40 <= len(rows) <= 60, "dataset must hold 40-60 items, found %d" % len(rows)
assert len({row["id"] for row in rows}) == len(rows), "ids must be unique"

for row in rows:
    assert set(row) >= {"id", "kind", "received_at", "thread_len", "text", "labels", "evidence"}, row["id"]
    assert row["kind"] in KINDS, row["id"]
    assert row["thread_len"] >= 1, row["id"]
    assert row["text"].strip(), row["id"]
    labels = row["labels"]
    assert labels["classification"] in CLASSES, row["id"]
    assert labels["priority"] in PRIORITIES, row["id"]
    assert isinstance(labels["todo"], bool) and isinstance(labels["draft"], bool), row["id"]
    assert row["evidence"], "every item needs evidence: " + row["id"]
    for span in row["evidence"]:
        assert span in row["text"], "evidence must be verbatim in %s: %r" % (row["id"], span)
print("DATASET_SHAPE_AND_VERBATIM_EVIDENCE=PASS items=%d" % len(rows))

counts = collections.Counter(row["labels"]["classification"] for row in rows)
for name in CLASSES:
    assert counts[name] >= 5, "class %s needs at least 5 samples, found %d" % (name, counts[name])
print("DATASET_CLASS_COVERAGE=PASS " + json.dumps(dict(sorted(counts.items())), ensure_ascii=False))

long_threads = sum(1 for row in rows if row["thread_len"] >= 5)
assert long_threads >= 5, long_threads
groups = collections.Counter(hashlib.sha256(row["text"].encode("utf-8")).hexdigest() for row in rows)
duplicates = sum(1 for count in groups.values() if count > 1)
assert duplicates >= 3, duplicates
vague = sum(1 for row in rows if any(marker in row["text"] for marker in ("有空", "下次", "回头聊", "sometime", "no rush", "不急")))
assert vague >= 2, vague
urgent = sum(1 for row in rows if row["labels"]["priority"] == "high")
assert urgent >= 10, urgent
print("DATASET_COVERAGE_LONG_THREADS_DUPES_VAGUE=PASS long=%d dup_groups=%d vague=%d high=%d"
      % (long_threads, duplicates, vague, urgent))

injection = [row for row in rows if row["labels"]["classification"] == "injection"]
assert len(injection) >= 5, len(injection)
for row in injection:
    assert row["labels"]["todo"] is False and row["labels"]["draft"] is False, "injection must not create work: " + row["id"]
print("DATASET_INJECTION_ITEMS_CREATE_NO_WORK=PASS n=%d" % len(injection))

print("M1_DATASET=PASS")
