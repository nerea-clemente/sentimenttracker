"""Row selection shared by the metric modules.

One place decides what "the articles for this campaign at day N" means, so every metric truncates
the same way. An unfair comparison is worse than no comparison, and inconsistent truncation is the
easiest way to produce one.
"""

from __future__ import annotations

import sqlite3

from ..db import query, query_one

_ARTICLE_COLUMNS = """
    a.id, a.campaign_id, a.outlet_id, a.url, a.headline, a.published_at, a.day_index,
    a.language, a.country, a.byline, a.body_hash, a.word_count, a.cluster_id, a.is_original,
    a.import_id, a.retrieved_at,
    o.name AS outlet_name, o.domain AS outlet_domain, o.tier AS outlet_tier,
    o.country AS outlet_country, o.language AS outlet_language,
    o.reach_value, o.reach_source, o.reach_is_estimated,
    o.is_known_syndication_partner
"""


def articles(conn: sqlite3.Connection, campaign_id: int,
             at_day_index: int | None = None) -> list[sqlite3.Row]:
    """Articles for a campaign, optionally truncated at a day index (inclusive).

    When a cutoff is given, articles with no day_index are excluded: they cannot be placed on the
    shared axis, so including them would silently make one campaign's window wider than another's.
    They are counted separately by `undated_count` and surfaced as a data-quality figure.
    """
    if at_day_index is None:
        return query(conn, f"""
            SELECT {_ARTICLE_COLUMNS}
              FROM articles a JOIN outlets o ON o.id = a.outlet_id
             WHERE a.campaign_id = ?
             ORDER BY a.published_at, a.id
        """, (campaign_id,))
    return query(conn, f"""
        SELECT {_ARTICLE_COLUMNS}
          FROM articles a JOIN outlets o ON o.id = a.outlet_id
         WHERE a.campaign_id = ? AND a.day_index IS NOT NULL AND a.day_index <= ?
         ORDER BY a.published_at, a.id
    """, (campaign_id, at_day_index))


def undated_count(conn: sqlite3.Connection, campaign_id: int) -> int:
    row = query_one(
        conn,
        "SELECT COUNT(*) AS n FROM articles WHERE campaign_id = ? AND day_index IS NULL",
        (campaign_id,),
    )
    return int(row["n"]) if row else 0


def escalations(conn: sqlite3.Connection, campaign_id: int, published_at: str | None = None,
                timezone: str = "UTC", at_day_index: int | None = None) -> list[sqlite3.Row]:
    rows = query(conn, """
        SELECT * FROM escalations WHERE campaign_id = ? ORDER BY occurred_at, id
    """, (campaign_id,))
    if at_day_index is None or published_at is None:
        return rows
    from ..timeutil import day_index as compute
    kept = []
    for r in rows:
        di = compute(published_at, r["occurred_at"], timezone)
        if di is not None and di <= at_day_index:
            kept.append(r)
    return kept


def own_company_mentions(conn: sqlite3.Connection, campaign_id: int,
                         at_day_index: int | None = None) -> list[sqlite3.Row]:
    return mentions_for_entity_type(conn, campaign_id, "own_company", at_day_index)


def mentions_for_entity_type(conn: sqlite3.Connection, campaign_id: int, entity_type: str,
                             at_day_index: int | None = None) -> list[sqlite3.Row]:
    cutoff_sql = "" if at_day_index is None else "AND a.day_index IS NOT NULL AND a.day_index <= ?"
    params: tuple = (campaign_id, entity_type) if at_day_index is None else (
        campaign_id, entity_type, at_day_index
    )
    return query(conn, f"""
        SELECT m.id, m.article_id, m.entity_id, m.role, m.evidence_sentence, m.confidence,
               m.classified_by, e.name AS entity_name, e.type AS entity_type,
               a.day_index, a.published_at, a.headline, a.url, a.outlet_id
          FROM mentions m
          JOIN entities e ON e.id = m.entity_id
          JOIN articles a ON a.id = m.article_id
         WHERE a.campaign_id = ? AND e.type = ? {cutoff_sql}
         ORDER BY a.day_index, m.id
    """, params)


def articles_with_body(conn: sqlite3.Connection, campaign_id: int) -> tuple[int, int]:
    """(articles with body text, total articles) — the denominator for mention coverage."""
    row = query_one(conn, """
        SELECT SUM(CASE WHEN body_text IS NOT NULL AND length(body_text) > 0 THEN 1 ELSE 0 END)
                   AS with_body,
               COUNT(*) AS total
          FROM articles WHERE campaign_id = ?
    """, (campaign_id,))
    if not row or not row["total"]:
        return (0, 0)
    return (int(row["with_body"] or 0), int(row["total"]))
