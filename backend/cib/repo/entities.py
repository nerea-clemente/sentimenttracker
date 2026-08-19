"""Entity and mention reads and writes."""

from __future__ import annotations

import json
import sqlite3

from ..actor import actor, now_utc
from ..db import insert, query, query_one
from ..models import Entity


def create(conn: sqlite3.Connection, *, name: str, type: str,
           aliases: list[str] | None = None, notes: str | None = None) -> int:
    return insert(conn, "entities", {
        "name": name.strip(),
        "type": type,
        "aliases": json.dumps(sorted({a.strip() for a in (aliases or []) if a.strip()})),
        "notes": notes,
        "created_at": now_utc(),
        "created_by": actor(),
    })


def get_or_create(conn: sqlite3.Connection, *, name: str, type: str,
                  aliases: list[str] | None = None) -> int:
    row = query_one(conn, "SELECT id FROM entities WHERE name = ?", (name.strip(),))
    if row:
        return int(row["id"])
    return create(conn, name=name, type=type, aliases=aliases)


def get(conn: sqlite3.Connection, entity_id: int) -> Entity | None:
    return Entity.from_row(query_one(conn, "SELECT * FROM entities WHERE id = ?", (entity_id,)))


def get_by_name(conn: sqlite3.Connection, name: str) -> Entity | None:
    return Entity.from_row(query_one(conn, "SELECT * FROM entities WHERE name = ?", (name,)))


def list_all(conn: sqlite3.Connection, type: str | None = None) -> list[Entity]:
    if type:
        rows = query(conn, "SELECT * FROM entities WHERE type = ? ORDER BY name", (type,))
    else:
        rows = query(conn, "SELECT * FROM entities ORDER BY type, name")
    return [Entity.from_row(r) for r in rows]


def own_company(conn: sqlite3.Connection) -> Entity | None:
    """The entity this tool measures exposure for. There should be exactly one."""
    return Entity.from_row(
        query_one(conn, "SELECT * FROM entities WHERE type = 'own_company' ORDER BY id LIMIT 1")
    )


def add_alias(conn: sqlite3.Connection, entity_id: int, alias: str) -> None:
    ent = get(conn, entity_id)
    if ent is None:
        raise LookupError(f"No entity {entity_id}")
    aliases = sorted({*ent.alias_list, alias.strip()})
    conn.execute("UPDATE entities SET aliases = ? WHERE id = ?", (json.dumps(aliases), entity_id))


def add_mention(conn: sqlite3.Connection, *, article_id: int, entity_id: int, role: str,
                evidence_sentence: str, char_offset: int | None = None,
                confidence: float | None = None, classified_by: str = "rule") -> int | None:
    """Record a mention. Returns None if this exact mention is already recorded.

    evidence_sentence is mandatory by schema: a mention that cannot be quoted back cannot be
    defended when the number is challenged.
    """
    if not evidence_sentence or not evidence_sentence.strip():
        raise ValueError("evidence_sentence is required for every mention")
    if classified_by == "llm" and confidence is None:
        raise ValueError("an LLM-assigned role must carry a confidence value so it can be reviewed")
    try:
        return insert(conn, "mentions", {
            "article_id": article_id,
            "entity_id": entity_id,
            "role": role,
            "evidence_sentence": evidence_sentence.strip()[:1000],
            "char_offset": char_offset,
            "confidence": confidence,
            "classified_by": classified_by,
            "created_at": now_utc(),
            "created_by": actor(),
        })
    except sqlite3.IntegrityError:
        return None


def mentions_for_article(conn: sqlite3.Connection, article_id: int) -> list[sqlite3.Row]:
    return query(conn, """
        SELECT m.*, e.name AS entity_name, e.type AS entity_type
          FROM mentions m JOIN entities e ON e.id = m.entity_id
         WHERE m.article_id = ?
         ORDER BY m.char_offset
    """, (article_id,))


def clear_for_campaign(conn: sqlite3.Connection, campaign_id: int,
                       classified_by: str | None = None) -> int:
    """Delete machine-assigned mentions so they can be recomputed. Human ones are never touched
    unless explicitly named."""
    if classified_by:
        cur = conn.execute("""
            DELETE FROM mentions WHERE classified_by = ? AND article_id IN
                (SELECT id FROM articles WHERE campaign_id = ?)
        """, (classified_by, campaign_id))
    else:
        cur = conn.execute("""
            DELETE FROM mentions WHERE classified_by <> 'human' AND article_id IN
                (SELECT id FROM articles WHERE campaign_id = ?)
        """, (campaign_id,))
    return cur.rowcount
