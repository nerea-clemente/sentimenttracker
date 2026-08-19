"""Outlet reads and writes, including reach provenance."""

from __future__ import annotations

import sqlite3

from ..actor import actor, now_utc
from ..db import insert, query, query_one
from ..models import Outlet
from ..textutil import domain_of


def get_or_create(
    conn: sqlite3.Connection,
    *,
    name: str,
    domain: str | None = None,
    country: str | None = None,
    language: str | None = None,
    tier: str = "blog",
    is_known_syndication_partner: int = 0,
) -> int:
    """Find an outlet by domain, then by normalised name. Create it if neither matches.

    Nothing here invents a reach value: a newly created outlet has reach_value NULL until someone
    sets it with a named source.
    """
    dom = domain_of(domain) or domain_of(name)
    if dom:
        row = query_one(conn, "SELECT id FROM outlets WHERE domain = ?", (dom,))
        if row:
            return int(row["id"])
    row = query_one(
        conn,
        "SELECT id FROM outlets WHERE lower(trim(name)) = ?",
        (name.strip().lower(),),
    )
    if row:
        return int(row["id"])
    return insert(conn, "outlets", {
        "name": name.strip(),
        "domain": dom,
        "country": country,
        "language": language,
        "tier": tier,
        "is_known_syndication_partner": is_known_syndication_partner,
        "created_at": now_utc(),
        "created_by": actor(),
    })


def get(conn: sqlite3.Connection, outlet_id: int) -> Outlet | None:
    return Outlet.from_row(query_one(conn, "SELECT * FROM outlets WHERE id = ?", (outlet_id,)))


def list_all(conn: sqlite3.Connection) -> list[Outlet]:
    return [Outlet.from_row(r) for r in query(conn, "SELECT * FROM outlets ORDER BY name")]


def set_reach(
    conn: sqlite3.Connection,
    outlet_id: int,
    *,
    reach_value: int,
    reach_source: str,
    is_estimated: bool = False,
    estimation_method: str | None = None,
    as_of: str | None = None,
) -> None:
    """Record a reach figure. A source is mandatory; an estimate must name its method.

    The database enforces both constraints too — this is the readable half of that guard.
    """
    if not reach_source or not reach_source.strip():
        raise ValueError("reach_source is required: reach figures must name where they came from")
    if is_estimated and not (estimation_method and estimation_method.strip()):
        raise ValueError("estimation_method is required when reach_is_estimated is true")
    conn.execute("""
        UPDATE outlets
           SET reach_value = ?, reach_source = ?, reach_is_estimated = ?,
               reach_estimation_method = ?, reach_as_of = ?
         WHERE id = ?
    """, (int(reach_value), reach_source.strip(), 1 if is_estimated else 0,
          estimation_method, as_of, outlet_id))


def set_tier(conn: sqlite3.Connection, outlet_id: int, tier: str) -> None:
    conn.execute("UPDATE outlets SET tier = ? WHERE id = ?", (tier, outlet_id))
