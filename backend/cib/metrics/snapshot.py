"""Daily snapshots into metrics_daily.

Snapshots exist so the day-aligned charts stay cheap and so a campaign's shape is preserved even
if articles are later re-clustered. They are derived, never authoritative: `cib snapshot` rebuilds
them from the article rows at any time.
"""

from __future__ import annotations

import sqlite3

from ..actor import now_utc
from ..db import query_one
from ..repo import campaigns as campaign_repo
from ..repo import entities as entity_repo
from ..timeutil import date_for_day_index, iso_date
from . import selectors
from .footprint import article_country, article_language


def rebuild(conn: sqlite3.Connection, campaign_id: int) -> int:
    """Recompute every metrics_daily row for one campaign. Returns rows written."""
    campaign = campaign_repo.get(conn, campaign_id)
    if campaign is None:
        raise LookupError(f"No campaign {campaign_id}")
    conn.execute("DELETE FROM metrics_daily WHERE campaign_id = ?", (campaign_id,))
    if campaign.published_at is None:
        return 0

    rows = selectors.articles(conn, campaign_id, None)
    dated = [r for r in rows if r["day_index"] is not None]
    if not dated:
        return 0

    own = entity_repo.own_company(conn)
    mention_days: dict[int, int] = {}
    if own is not None:
        for m in selectors.own_company_mentions(conn, campaign_id, None):
            if m["day_index"] is not None and int(m["entity_id"]) == own.id:
                d = int(m["day_index"])
                mention_days[d] = mention_days.get(d, 0) + 1

    by_day: dict[int, list] = {}
    for r in dated:
        by_day.setdefault(int(r["day_index"]), []).append(r)

    lo, hi = min(by_day), max(by_day)
    seen_outlets: set[int] = set()
    cumulative = 0
    written = 0
    stamp = now_utc()

    for day in range(lo, hi + 1):
        todays = by_day.get(day, [])
        cumulative += len(todays)
        seen_outlets.update(int(r["outlet_id"]) for r in todays)
        d = date_for_day_index(campaign.published_at, day, campaign.timezone)
        conn.execute("""
            INSERT INTO metrics_daily
                (campaign_id, date, day_index, article_count, cumulative_articles,
                 unique_outlets, cumulative_unique_outlets, countries, languages,
                 own_company_mentions, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            campaign_id,
            iso_date(d) if d else f"day{day}",
            day,
            len(todays),
            cumulative,
            len({int(r["outlet_id"]) for r in todays}),
            len(seen_outlets),
            len({c for c in (article_country(r) for r in todays) if c}),
            len({lang for lang in (article_language(r) for r in todays) if lang}),
            mention_days.get(day, 0),
            stamp,
        ))
        written += 1
    return written


def rebuild_live(conn: sqlite3.Connection) -> dict[str, int]:
    """Snapshot every campaign that is live or decaying. Run daily."""
    out: dict[str, int] = {}
    for status in ("live", "decaying"):
        for campaign in campaign_repo.list_all(conn, status=status):
            out[campaign.slug] = rebuild(conn, campaign.id)
    return out


def rebuild_all(conn: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for campaign in campaign_repo.list_all(conn):
        if campaign.published_at:
            out[campaign.slug] = rebuild(conn, campaign.id)
    return out


def last_snapshot_at(conn: sqlite3.Connection, campaign_id: int) -> str | None:
    row = query_one(
        conn, "SELECT MAX(computed_at) AS t FROM metrics_daily WHERE campaign_id = ?",
        (campaign_id,),
    )
    return row["t"] if row else None
