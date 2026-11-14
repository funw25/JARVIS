"""Experimental plaintext audit log; events must never include secrets."""
import json
from datetime import datetime, timezone
from pathlib import Path


def audit(vault_root, event):
    path = Path(vault_root) / "audit.log"
    record = {"at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    record.update(event)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path
