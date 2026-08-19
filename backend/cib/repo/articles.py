"""Article writes, including the idempotent upsert that makes re-imports safe."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..actor import actor, now_utc
from ..db import insert, query, query_one
from ..models import Article
from ..textutil import body_hash as hash_body
from ..textutil import sha256_text, word_count
from ..timeutil import day_index as compute_day_index


@dataclass
class UpsertResult:
    article_id: int | None
    created: bool
    reason: str = ""


def _existing(conn: sqlite3.Connection, campaign_id: int, url: str | None,
              outlet_id: int, body_hash: str | None, published_at: str) -> int | None:
    if url:
        row = query_one(
            conn, "SELECT id FROM articles WHERE campaign_id = ? AND url = ?", (campaign_id, url)
        )
        return int(row["id"]) if row else None
    row = query_one(conn, """
        SELECT id FROM articles
         WHERE campaign_id = ? AND url IS NULL AND outlet_id = ?
           AND body_hash IS ? AND published_at = ?
    """, (campaign_id, outlet_id, body_hash, published_at))
    return int(row["id"]) if row else None


def upsert(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    outlet_id: int,
    headline: str,
    published_at: str,
    import_id: int,
    campaign_published_at: str | None = None,
    campaign_timezone: str = "UTC",
    url: str | None = None,
    language: str | None = None,
    country: str | None = None,
    byline: str | None = None,
    body_text: str | None = None,
    retrieved_at: str | None = None,
    source_row: dict | None = None,
) -> UpsertResult:
    """Insert an article, or report it as an already-present duplicate.

    Re-importing the same export file is a no-op: the natural key is (campaign, url), falling back
    to (campaign, outlet, body hash, publication time) for exports that carry no URL.
    """
    bh = hash_body(body_text)
    found = _existing(conn, campaign_id, url, outlet_id, bh, published_at)
    if found is not None:
        return UpsertResult(article_id=found, created=False, reason="already imported")

    fingerprint = None
    if source_row:
        canonical = "|".join(f"{k}={source_row[k]}" for k in sorted(source_row) if source_row[k])
        fingerprint = sha256_text(canonical)

    article_id = insert(conn, "articles", {
        "campaign_id": campaign_id,
        "outlet_id": outlet_id,
        "url": url,
        "headline": headline,
        "published_at": published_at,
        "day_index": compute_day_index(campaign_published_at, published_at, campaign_timezone),
        "language": language,
        "country": country,
        "byline": byline,
        "body_text": body_text,
        "body_hash": bh,
        "word_count": word_count(body_text),
        "cluster_id": None,
        "is_original": 1,
        "import_id": import_id,
        "row_fingerprint": fingerprint,
        "retrieved_at": retrieved_at or now_utc(),
        "created_at": now_utc(),
        "created_by": actor(),
    })
    return UpsertResult(article_id=article_id, created=True)


def get(conn: sqlite3.Connection, article_id: int) -> Article | None:
    return Article.from_row(query_one(conn, "SELECT * FROM articles WHERE id = ?", (article_id,)))


def for_campaign(conn: sqlite3.Connection, campaign_id: int,
                 at_day_index: int | None = None) -> list[Article]:
    if at_day_index is None:
        rows = query(conn, "SELECT * FROM articles WHERE campaign_id = ? ORDER BY published_at",
                     (campaign_id,))
    else:
        rows = query(conn, """
            SELECT * FROM articles
             WHERE campaign_id = ? AND day_index IS NOT NULL AND day_index <= ?
             ORDER BY published_at
        """, (campaign_id, at_day_index))
    return [Article.from_row(r) for r in rows]


def recompute_day_index(conn: sqlite3.Connection, campaign_id: int,
                        published_at: str | None, timezone: str) -> int:
    """Rebuild the cached day_index for a campaign. Needed whenever published_at changes."""
    updated = 0
    for row in query(conn, "SELECT id, published_at FROM articles WHERE campaign_id = ?",
                     (campaign_id,)):
        di = compute_day_index(published_at, row["published_at"], timezone)
        conn.execute("UPDATE articles SET day_index = ? WHERE id = ?", (di, row["id"]))
        updated += 1
    return updated


def with_missing_day_index(conn: sqlite3.Connection, campaign_id: int) -> int:
    """Articles we could not place on the day axis — a data-quality figure, not a metric."""
    row = query_one(
        conn,
        "SELECT COUNT(*) AS n FROM articles WHERE campaign_id = ? AND day_index IS NULL",
        (campaign_id,),
    )
    return int(row["n"]) if row else 0
