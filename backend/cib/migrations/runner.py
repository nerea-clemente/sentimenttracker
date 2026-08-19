"""Migration runner.

Applies numbered .sql files in order and records each in schema_migrations. Migrations are plain
SQL files so the schema can be read and reasoned about without running Python.
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from ..actor import actor, now_utc

MIGRATIONS_DIR = Path(__file__).resolve().parent

_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    TEXT PRIMARY KEY,
    filename   TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    applied_by TEXT NOT NULL
)
"""


def _files() -> list[Path]:
    return sorted(p for p in MIGRATIONS_DIR.glob("*.sql"))


def applied(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    conn.execute(_LEDGER)
    return {r["version"]: r for r in conn.execute("SELECT * FROM schema_migrations")}


def migrate(conn: sqlite3.Connection, verbose: bool = False) -> list[str]:
    """Apply any unapplied migrations. Returns the versions applied in this run."""
    done = applied(conn)
    newly: list[str] = []
    for path in _files():
        version = path.stem.split("_", 1)[0]
        sql = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if version in done:
            if done[version]["sha256"] != digest:
                raise RuntimeError(
                    f"Migration {path.name} has changed since it was applied "
                    f"({done[version]['sha256'][:12]} -> {digest[:12]}). "
                    "Add a new migration instead of editing an applied one."
                )
            continue
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, filename, sha256, applied_at, applied_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (version, path.name, digest, now_utc(), actor()),
        )
        newly.append(version)
        if verbose:
            print(f"applied {path.name}")
    return newly
