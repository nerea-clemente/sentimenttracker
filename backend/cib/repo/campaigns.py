"""Campaign reads and writes."""

from __future__ import annotations

import json
import sqlite3

from ..actor import actor, now_utc
from ..db import insert, query, query_one
from ..models import Campaign


def _slugify(name: str) -> str:
    out = []
    for ch in name.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:80] or "campaign"


def create(
    conn: sqlite3.Connection,
    *,
    name: str,
    publisher_org: str,
    campaign_type: str,
    status: str,
    published_at: str | None = None,
    published_at_precision: str = "day",
    first_signal_at: str | None = None,
    timezone: str = "Europe/Copenhagen",
    themes: list[str] | None = None,
    notes: str | None = None,
    slug: str | None = None,
) -> int:
    return insert(conn, "campaigns", {
        "name": name,
        "slug": slug or _slugify(name),
        "publisher_org": publisher_org,
        "campaign_type": campaign_type,
        "status": status,
        "published_at": published_at,
        "published_at_precision": published_at_precision,
        "first_signal_at": first_signal_at,
        "timezone": timezone,
        "themes": json.dumps(themes or []),
        "notes": notes,
        "created_at": now_utc(),
        "created_by": actor(),
    })


def get(conn: sqlite3.Connection, campaign_id: int) -> Campaign | None:
    return Campaign.from_row(query_one(conn, "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)))


def get_by_slug(conn: sqlite3.Connection, slug: str) -> Campaign | None:
    return Campaign.from_row(query_one(conn, "SELECT * FROM campaigns WHERE slug = ?", (slug,)))


def resolve(conn: sqlite3.Connection, ref: str | int) -> Campaign:
    """Resolve a campaign by numeric id or slug. Raises rather than returning None."""
    found = None
    if isinstance(ref, int) or str(ref).isdigit():
        found = get(conn, int(ref))
    if found is None:
        found = get_by_slug(conn, str(ref))
    if found is None:
        raise LookupError(f"No campaign with id or slug {ref!r}")
    return found


def list_all(conn: sqlite3.Connection, status: str | None = None) -> list[Campaign]:
    if status:
        rows = query(conn, "SELECT * FROM campaigns WHERE status = ? ORDER BY id", (status,))
    else:
        rows = query(conn, "SELECT * FROM campaigns ORDER BY id")
    return [Campaign.from_row(r) for r in rows]


def set_published(conn: sqlite3.Connection, campaign_id: int, published_at: str,
                  status: str = "live", precision: str = "day") -> None:
    """Set a campaign's day zero. Also promotes it out of pre_publication status.

    Callers must recompute day_index afterwards (see articles.recompute_day_index) because the
    cached offsets on existing articles are now stale.
    """
    conn.execute(
        "UPDATE campaigns SET published_at = ?, status = ?, published_at_precision = ? "
        "WHERE id = ?",
        (published_at, status, precision, campaign_id),
    )


def set_status(conn: sqlite3.Connection, campaign_id: int, status: str) -> None:
    conn.execute("UPDATE campaigns SET status = ? WHERE id = ?", (status, campaign_id))


def add_precedent(conn: sqlite3.Connection, campaign_id: int, precedent_campaign_id: int,
                  rationale: str) -> int:
    return insert(conn, "publisher_precedents", {
        "campaign_id": campaign_id,
        "precedent_campaign_id": precedent_campaign_id,
        "rationale": rationale,
        "created_at": now_utc(),
        "created_by": actor(),
    })


def precedents(conn: sqlite3.Connection, campaign_id: int) -> list[sqlite3.Row]:
    return query(conn, """
        SELECT p.precedent_campaign_id, p.rationale, p.created_by, p.created_at,
               c.name, c.slug, c.publisher_org, c.published_at, c.status
        FROM publisher_precedents p
        JOIN campaigns c ON c.id = p.precedent_campaign_id
        WHERE p.campaign_id = ?
        ORDER BY c.published_at
    """, (campaign_id,))
