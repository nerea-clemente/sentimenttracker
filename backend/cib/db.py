"""SQLite access.

Hand-written SQL only — no ORM. This database will be queried by hand by people who need to
defend a number in a meeting, so the schema and the queries stay legible.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import config


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    target = Path(path) if path is not None else config.db_path()
    if str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False because FastAPI runs synchronous dependencies and endpoints on a
    # worker threadpool, so a connection opened during dependency setup can be used on a
    # different thread than it was created on. Each request still gets its own connection and
    # never shares one concurrently.
    conn = sqlite3.connect(str(target), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    # Wait rather than failing outright when the daily snapshot job and a request overlap.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit transaction. isolation_level=None means we control BEGIN/COMMIT ourselves."""
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def query(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
    return conn.execute(sql, params).fetchall()


def query_one(conn: sqlite3.Connection, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
    return conn.execute(sql, params).fetchone()


def scalar(conn: sqlite3.Connection, sql: str, params: tuple | dict = (), default=None):
    row = conn.execute(sql, params).fetchone()
    if row is None or row[0] is None:
        return default
    return row[0]


def insert(conn: sqlite3.Connection, table: str, values: dict) -> int:
    cols = ", ".join(values)
    placeholders = ", ".join(f":{c}" for c in values)
    cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", values)
    return int(cur.lastrowid)
