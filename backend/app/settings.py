from pathlib import Path
import os

APP_NAME = "Jarvis Local Core"
API_PREFIX = "/api/v1"

def data_dir() -> Path:
    # Development default only. Do not store real personal data before encryption is implemented.
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return Path(os.environ.get("JARVIS_DATA_DIR", root / "Jarvis"))

def database_path() -> Path:
    return data_dir() / "jarvis-dev.sqlite3"
