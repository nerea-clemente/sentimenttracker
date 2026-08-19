"""Configuration, loaded from the environment with a minimal .env reader.

Deliberately dependency-free: the core tool must run without installing anything.
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "cib.sqlite3"


def load_dotenv(path: Path | None = None) -> None:
    """Populate os.environ from a .env file. Existing environment variables always win."""
    env_path = path or (REPO_ROOT / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def db_path() -> Path:
    load_dotenv()
    raw = os.environ.get("CIB_DB_PATH")
    if not raw:
        return DEFAULT_DB_PATH
    p = Path(raw)
    return p if p.is_absolute() else REPO_ROOT / p


def get(name: str, default: str = "") -> str:
    load_dotenv()
    return os.environ.get(name, default)


def get_bool(name: str, default: bool = False) -> bool:
    raw = get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}
