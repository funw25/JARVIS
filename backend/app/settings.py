from pathlib import Path
import os

APP_NAME = "Jarvis Local Core"
API_PREFIX = "/api/v1"
VAULT_DB_NAME = "vault.db"


def data_dir() -> Path:
    # Development default only. Do not store real personal data before encryption is implemented.
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return Path(os.environ.get("JARVIS_DATA_DIR", root / "Jarvis"))


def vault_dir() -> Path | None:
    """Optional vault directory; when set the application uses the vault's database."""
    value = os.environ.get("JARVIS_VAULT_DIR", "").strip()
    return Path(value) if value else None


def database_path() -> Path:
    vault = vault_dir()
    if vault is not None:
        return vault / VAULT_DB_NAME
    return data_dir() / "jarvis-dev.sqlite3"
