"""Fail-closed development storage mode; this is not database encryption."""
import os


class StorageSecurityError(RuntimeError):
    pass


def require_synthetic_storage() -> None:
    mode = os.environ.get("JARVIS_DATA_MODE", "").strip().lower()
    if mode != "synthetic":
        raise StorageSecurityError(
            "Only explicitly enabled synthetic storage is available; personal-data encryption is not implemented"
        )
