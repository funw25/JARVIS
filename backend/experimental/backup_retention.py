"""Retention operations for synthetic backup archives and sinks."""
from pathlib import Path

from . import backup_sink


def validate_job_retention(keep_local, keep_remote):
    """A backup job must retain at least one local and one remote copy."""
    for name, value in (("keep_local", keep_local), ("keep_remote", keep_remote)):
        if type(value) is not int or value < 1:
            raise ValueError("%s must be a positive integer" % name)


def _validate_prune_count(keep):
    if type(keep) is not int or keep < 0:
        raise ValueError("keep must be a non-negative integer")


def prune_local(directory, keep):
    _validate_prune_count(keep)
    archives = sorted(
        Path(directory).glob("jarvis-backup-*.jvault"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed = []
    for old in archives[keep:]:
        old.unlink()
        removed.append(old.name)
    return removed


def prune_remote(sink, keep):
    _validate_prune_count(keep)
    removed, locked = [], []
    prefix = getattr(sink, "prefix", "")
    def is_backup(item):
        key = item.get("key")
        if not isinstance(key, str) or not key.startswith(prefix):
            return False
        name = key[len(prefix):]
        return ("/" not in name and "\\" not in name and
                name.startswith("jarvis-backup-") and name.endswith(".jvault"))
    items = sorted((item for item in sink.list() if is_backup(item)),
                   key=lambda item: item.get("modified") or item["key"], reverse=True)
    for item in items[keep:]:
        try:
            sink.delete(item["key"])
            removed.append(item["key"])
        except backup_sink.SinkLocked:
            locked.append(item["key"])
    return removed, locked
