"""Write-path attribution.

Every row in this database records who or what wrote it. `actor()` resolves that identity once,
from an explicit override, then CIB_ACTOR, then the OS user.
"""

from __future__ import annotations

import getpass
import os
from datetime import UTC, datetime

_override: str | None = None


def set_actor(name: str | None) -> None:
    global _override
    _override = name.strip() if name and name.strip() else None


def actor() -> str:
    if _override:
        return _override
    env = os.environ.get("CIB_ACTOR", "").strip()
    if env:
        return env
    try:
        return f"user:{getpass.getuser()}"
    except Exception:
        return "user:unknown"


def now_utc() -> str:
    """ISO-8601 UTC timestamp, second precision, used for every *_at audit column."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
