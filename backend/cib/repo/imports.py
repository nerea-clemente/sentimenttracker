"""The import ledger. Every article traces back to a row here."""

from __future__ import annotations

import json
import sqlite3

from ..actor import actor, now_utc
from ..db import insert, query, query_one


def start(conn: sqlite3.Connection, *, source: str, file_name: str | None = None,
          file_hash: str | None = None, mapping: dict | None = None,
          notes: str | None = None) -> int:
    return insert(conn, "imports", {
        "source": source,
        "file_name": file_name,
        "file_hash": file_hash,
        "imported_at": now_utc(),
        "row_count": 0,
        "rows_inserted": 0,
        "rows_skipped_duplicate": 0,
        "rows_rejected": 0,
        "mapping": json.dumps(mapping) if mapping else None,
        "notes": notes,
        "created_by": actor(),
    })


def finish(conn: sqlite3.Connection, import_id: int, *, row_count: int, inserted: int,
           skipped: int, rejected: int, notes: str | None = None) -> None:
    conn.execute("""
        UPDATE imports
           SET row_count = ?, rows_inserted = ?, rows_skipped_duplicate = ?,
               rows_rejected = ?, notes = COALESCE(?, notes)
         WHERE id = ?
    """, (row_count, inserted, skipped, rejected, notes, import_id))


def get(conn: sqlite3.Connection, import_id: int) -> sqlite3.Row | None:
    return query_one(conn, "SELECT * FROM imports WHERE id = ?", (import_id,))


def list_all(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return query(conn, "SELECT * FROM imports ORDER BY id DESC LIMIT ?", (limit,))


def previous_with_hash(conn: sqlite3.Connection, file_hash: str) -> sqlite3.Row | None:
    """A prior import of a byte-identical file, if any. Used to report a no-op re-import."""
    return query_one(
        conn,
        "SELECT * FROM imports WHERE file_hash = ? ORDER BY id LIMIT 1",
        (file_hash,),
    )


def last_import_at(conn: sqlite3.Connection, campaign_id: int | None = None) -> str | None:
    """Timestamp of the most recent import, for the data-quality banner."""
    if campaign_id is None:
        return query_one(conn, "SELECT MAX(imported_at) AS t FROM imports")["t"]
    row = query_one(conn, """
        SELECT MAX(i.imported_at) AS t
          FROM imports i
          JOIN articles a ON a.import_id = i.id
         WHERE a.campaign_id = ?
    """, (campaign_id,))
    return row["t"] if row else None


def sources_for_campaign(conn: sqlite3.Connection, campaign_id: int) -> list[sqlite3.Row]:
    return query(conn, """
        SELECT i.source, COUNT(a.id) AS articles, MAX(i.imported_at) AS last_imported_at,
               COUNT(DISTINCT i.id) AS import_count
          FROM imports i
          JOIN articles a ON a.import_id = i.id
         WHERE a.campaign_id = ?
         GROUP BY i.source
         ORDER BY articles DESC
    """, (campaign_id,))
