"""Watch rule CRUD and matching."""

from __future__ import annotations

import json
import sqlite3

from ..actor import actor, now_utc
from ..db import insert, query, query_one
from ..models import WatchRule
from ..textutil import domain_of, normalise


def create(
    conn: sqlite3.Connection,
    *,
    name: str,
    rule_type: str,
    pattern: str,
    campaign_id: int | None = None,
    source_url: str | None = None,
    enabled: bool = True,
    promotes_to_live: bool = False,
    notes: str | None = None,
) -> int:
    return insert(conn, "watch_rules", {
        "campaign_id": campaign_id,
        "name": name,
        "rule_type": rule_type,
        "pattern": pattern,
        "source_url": source_url,
        "enabled": 1 if enabled else 0,
        "promotes_to_live": 1 if promotes_to_live else 0,
        "notes": notes,
        "created_at": now_utc(),
        "created_by": actor(),
    })


def list_all(conn: sqlite3.Connection, enabled_only: bool = False,
             campaign_id: int | None = None) -> list[WatchRule]:
    clauses, params = [], []
    if enabled_only:
        clauses.append("enabled = 1")
    if campaign_id is not None:
        clauses.append("campaign_id = ?")
        params.append(campaign_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return [WatchRule.from_row(r)
            for r in query(conn, f"SELECT * FROM watch_rules {where} ORDER BY id", tuple(params))]


def get(conn: sqlite3.Connection, rule_id: int) -> WatchRule | None:
    return WatchRule.from_row(query_one(conn, "SELECT * FROM watch_rules WHERE id = ?", (rule_id,)))


def set_enabled(conn: sqlite3.Connection, rule_id: int, enabled: bool) -> None:
    conn.execute("UPDATE watch_rules SET enabled = ? WHERE id = ?",
                 (1 if enabled else 0, rule_id))


def mark_polled(conn: sqlite3.Connection, rule_id: int, error: str | None = None) -> None:
    conn.execute("UPDATE watch_rules SET last_polled_at = ?, last_error = ? WHERE id = ?",
                 (now_utc(), error, rule_id))


def matches(rule: WatchRule, *, title: str | None, url: str | None,
            author: str | None = None, summary: str | None = None) -> bool:
    """Does one feed item satisfy one rule?

    Feed-shaped rules (rss, sitemap, gdelt_query) match every item their own feed returns — the
    feed *is* the filter. Content rules test the item's text.
    """
    if rule.rule_type in ("rss", "sitemap", "gdelt_query"):
        return True
    pattern = normalise(rule.pattern)
    if not pattern:
        return False
    if rule.rule_type == "domain":
        host = domain_of(url)
        target = domain_of(rule.pattern) or rule.pattern.lower()
        return bool(host and (host == target or host.endswith(f".{target}")))
    if rule.rule_type == "byline":
        return pattern in normalise(author or "")
    haystack = normalise(f"{title or ''} {summary or ''}")
    if rule.rule_type == "phrase":
        return pattern in haystack
    # keyword: every word must appear, in any order
    return all(word in haystack.split() for word in pattern.split())


def record_hit(
    conn: sqlite3.Connection,
    *,
    rule_id: int,
    matched_url: str,
    matched_title: str | None,
    occurred_at: str | None,
    matched_excerpt: str | None = None,
    raw: dict | None = None,
) -> int | None:
    """Store a hit. Returns None if this rule has already hit on this URL.

    Re-polling a feed must never re-alert on an item already seen.
    """
    try:
        return insert(conn, "watch_hits", {
            "rule_id": rule_id,
            "occurred_at": occurred_at or now_utc(),
            "detected_at": now_utc(),
            "matched_url": matched_url,
            "matched_title": matched_title,
            "matched_excerpt": (matched_excerpt or "")[:1000] or None,
            "raw": json.dumps(raw, ensure_ascii=False) if raw else None,
            "created_at": now_utc(),
        })
    except sqlite3.IntegrityError:
        return None


def recent_hits(conn: sqlite3.Connection, limit: int = 50,
                campaign_id: int | None = None) -> list[sqlite3.Row]:
    if campaign_id is None:
        return query(conn, """
            SELECT h.*, r.name AS rule_name, r.campaign_id, r.promotes_to_live
              FROM watch_hits h JOIN watch_rules r ON r.id = h.rule_id
             ORDER BY h.detected_at DESC LIMIT ?
        """, (limit,))
    return query(conn, """
        SELECT h.*, r.name AS rule_name, r.campaign_id, r.promotes_to_live
          FROM watch_hits h JOIN watch_rules r ON r.id = h.rule_id
         WHERE r.campaign_id = ? ORDER BY h.detected_at DESC LIMIT ?
    """, (campaign_id, limit))
