"""Syndication clustering.

The same story republished across forty outlets is forty outlets but one story. Reporting those
two numbers separately is the difference between "this is enormous" and "a wire picked it up".

Three passes, cheapest and most certain first:
  1. exact body hash          — identical text, certain
  2. normalised-title similarity ≥ 0.9 within a ±3 day window
  3. body shingle overlap ≥ 0.7 within the same window — catches lightly edited republication

Nothing is ever deleted. A duplicate keeps its row, gains a cluster_id, and has is_original set
to 0; the cluster_members row records which method matched, at what score, and against which
article, so a cluster can be audited and split by hand.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .actor import actor, now_utc
from .db import insert, query
from .textutil import shingle_similarity, token_set_ratio
from .timeutil import parse_timestamp

TITLE_THRESHOLD = 0.9
SHINGLE_THRESHOLD = 0.7
WINDOW_DAYS = 3


@dataclass
class ClusterReport:
    campaign_id: int
    clusters_created: int = 0
    articles_clustered: int = 0
    by_method: dict[str, int] = field(default_factory=dict)

    def note(self, method: str) -> None:
        self.by_method[method] = self.by_method.get(method, 0) + 1


def _within_window(a_at: str, b_at: str, days: int = WINDOW_DAYS) -> bool:
    da, db = parse_timestamp(a_at), parse_timestamp(b_at)
    if da is None or db is None:
        return False
    if da.tzinfo is not None:
        da = da.replace(tzinfo=None)
    if db.tzinfo is not None:
        db = db.replace(tzinfo=None)
    return abs((da - db).days) <= days


def _reset(conn: sqlite3.Connection, campaign_id: int) -> None:
    """Drop machine-made clusters so clustering can be re-run idempotently.

    Manual clusters survive: a human decision outranks the algorithm.
    """
    manual = {int(r["id"]) for r in query(
        conn, "SELECT id FROM clusters WHERE campaign_id = ? AND method = 'manual'", (campaign_id,)
    )}
    conn.execute("""
        UPDATE articles SET cluster_id = NULL, is_original = 1
         WHERE campaign_id = ? AND (cluster_id IS NULL OR cluster_id NOT IN
               (SELECT id FROM clusters WHERE campaign_id = ? AND method = 'manual'))
    """, (campaign_id, campaign_id))
    conn.execute("""
        DELETE FROM cluster_members WHERE cluster_id IN
            (SELECT id FROM clusters WHERE campaign_id = ? AND method <> 'manual')
    """, (campaign_id,))
    conn.execute("DELETE FROM clusters WHERE campaign_id = ? AND method <> 'manual'",
                 (campaign_id,))
    # Re-mark members of surviving manual clusters.
    for cid in manual:
        rows = query(conn, "SELECT article_id FROM cluster_members WHERE cluster_id = ?", (cid,))
        for r in rows:
            conn.execute("UPDATE articles SET cluster_id = ? WHERE id = ?", (cid, r["article_id"]))


def _new_cluster(conn: sqlite3.Connection, campaign_id: int, representative_id: int,
                 method: str) -> int:
    return insert(conn, "clusters", {
        "campaign_id": campaign_id,
        "representative_article_id": representative_id,
        "method": method,
        "member_count": 0,
        "created_at": now_utc(),
        "created_by": actor(),
    })


def _add_member(conn: sqlite3.Connection, cluster_id: int, article_id: int, method: str,
                similarity: float | None, matched_against: int | None,
                is_representative: bool) -> None:
    conn.execute("""
        INSERT OR IGNORE INTO cluster_members
            (cluster_id, article_id, method, similarity, matched_against_article_id,
             created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (cluster_id, article_id, method, similarity, matched_against, now_utc(), actor()))
    conn.execute(
        "UPDATE articles SET cluster_id = ?, is_original = ? WHERE id = ?",
        (cluster_id, 1 if is_representative else 0, article_id),
    )


def _refresh_counts(conn: sqlite3.Connection, campaign_id: int) -> None:
    conn.execute("""
        UPDATE clusters SET member_count = (
            SELECT COUNT(*) FROM cluster_members cm WHERE cm.cluster_id = clusters.id
        ) WHERE campaign_id = ?
    """, (campaign_id,))


def cluster_campaign(conn: sqlite3.Connection, campaign_id: int,
                     reset: bool = True) -> ClusterReport:
    """Detect syndication clusters for one campaign. Safe to re-run."""
    report = ClusterReport(campaign_id=campaign_id)
    if reset:
        _reset(conn, campaign_id)

    rows = query(conn, """
        SELECT id, headline, published_at, body_hash, body_text, cluster_id, outlet_id
          FROM articles WHERE campaign_id = ?
         ORDER BY published_at, id
    """, (campaign_id,))
    if not rows:
        return report

    # Article id -> cluster id, seeded with any surviving manual assignments.
    assigned: dict[int, int] = {int(r["id"]): int(r["cluster_id"]) for r in rows if r["cluster_id"]}

    # Pass 1 — exact body hash.
    by_hash: dict[str, list[int]] = {}
    for r in rows:
        if r["body_hash"]:
            by_hash.setdefault(r["body_hash"], []).append(int(r["id"]))
    for _hash, ids in by_hash.items():
        members = [i for i in ids if i not in assigned]
        if len(members) < 2:
            continue
        rep = members[0]
        cid = _new_cluster(conn, campaign_id, rep, "hash")
        report.clusters_created += 1
        for aid in members:
            _add_member(conn, cid, aid, "hash", 1.0, rep, aid == rep)
            assigned[aid] = cid
            report.articles_clustered += 1
            report.note("hash")

    # Passes 2 and 3 — title similarity, then body shingles, within a ±3 day window.
    unassigned = [r for r in rows if int(r["id"]) not in assigned]
    for i, row in enumerate(unassigned):
        aid = int(row["id"])
        if aid in assigned:
            continue
        matches: list[tuple[int, str, float]] = []
        for other in unassigned[i + 1:]:
            oid = int(other["id"])
            if oid in assigned:
                continue
            if not _within_window(row["published_at"], other["published_at"]):
                continue
            title_score = token_set_ratio(row["headline"], other["headline"])
            if title_score >= TITLE_THRESHOLD:
                matches.append((oid, "title_similarity", title_score))
                continue
            if row["body_text"] and other["body_text"]:
                shingle_score = shingle_similarity(row["body_text"], other["body_text"])
                if shingle_score >= SHINGLE_THRESHOLD:
                    matches.append((oid, "shingle", shingle_score))
        if not matches:
            continue
        method = "title_similarity" if any(m[1] == "title_similarity" for m in matches) else "shingle"
        cid = _new_cluster(conn, campaign_id, aid, method)
        report.clusters_created += 1
        _add_member(conn, cid, aid, method, 1.0, None, True)
        assigned[aid] = cid
        report.articles_clustered += 1
        report.note(method)
        for oid, m_method, score in matches:
            _add_member(conn, cid, oid, m_method, score, aid, False)
            assigned[oid] = cid
            report.articles_clustered += 1
            report.note(m_method)

    _refresh_counts(conn, campaign_id)
    return report


def merge_manual(conn: sqlite3.Connection, campaign_id: int, article_ids: list[int],
                 representative_id: int | None = None) -> int:
    """Force a set of articles into one cluster. A human decision, recorded as method='manual'."""
    if len(article_ids) < 2:
        raise ValueError("a manual cluster needs at least two articles")
    rep = representative_id or article_ids[0]
    cid = _new_cluster(conn, campaign_id, rep, "manual")
    for aid in article_ids:
        _add_member(conn, cid, aid, "manual", None, rep, aid == rep)
    _refresh_counts(conn, campaign_id)
    return cid


def split_out(conn: sqlite3.Connection, article_id: int) -> None:
    """Remove one article from its cluster — an audit correction, not a deletion."""
    conn.execute("DELETE FROM cluster_members WHERE article_id = ?", (article_id,))
    conn.execute("UPDATE articles SET cluster_id = NULL, is_original = 1 WHERE id = ?",
                 (article_id,))
    conn.execute("""
        UPDATE clusters SET member_count = (
            SELECT COUNT(*) FROM cluster_members cm WHERE cm.cluster_id = clusters.id
        )
    """)
