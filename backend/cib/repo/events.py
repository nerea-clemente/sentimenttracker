"""Escalations and inbound signals — the manually logged half of the model."""

from __future__ import annotations

import sqlite3

from ..actor import actor, now_utc
from ..db import insert, query, query_one
from ..models import SEVERITY_ANCHORS


def add_escalation(
    conn: sqlite3.Connection,
    *,
    campaign_id: int,
    occurred_at: str,
    escalation_type: str,
    actor_name: str,
    description: str,
    source_url: str,
    severity: int,
    actor_type: str | None = None,
) -> int:
    """Log a downstream consequence event. A source URL is required — an escalation is a claim
    about the outside world."""
    if not source_url or not source_url.strip():
        raise ValueError("source_url is required for every escalation")
    if int(severity) not in SEVERITY_ANCHORS:
        raise ValueError(f"severity must be one of {sorted(SEVERITY_ANCHORS)}")
    return insert(conn, "escalations", {
        "campaign_id": campaign_id,
        "occurred_at": occurred_at,
        "escalation_type": escalation_type,
        "actor_name": actor_name,
        "actor_type": actor_type,
        "description": description,
        "source_url": source_url.strip(),
        "severity": int(severity),
        "created_at": now_utc(),
        "created_by": actor(),
    })


def verify_escalation(conn: sqlite3.Connection, escalation_id: int,
                      verified_by: str | None = None) -> None:
    conn.execute(
        "UPDATE escalations SET verified_by = ?, verified_at = ? WHERE id = ?",
        (verified_by or actor(), now_utc(), escalation_id),
    )


def escalations_for(conn: sqlite3.Connection, campaign_id: int) -> list[sqlite3.Row]:
    return query(conn, "SELECT * FROM escalations WHERE campaign_id = ? ORDER BY occurred_at",
                 (campaign_id,))


def get_escalation(conn: sqlite3.Connection, escalation_id: int) -> sqlite3.Row | None:
    return query_one(conn, "SELECT * FROM escalations WHERE id = ?", (escalation_id,))


def add_inbound_signal(
    conn: sqlite3.Connection,
    *,
    occurred_at: str,
    channel: str,
    summary: str,
    campaign_id: int | None = None,
    source_ref: str | None = None,
    logged_by: str | None = None,
) -> int:
    return insert(conn, "inbound_signals", {
        "campaign_id": campaign_id,
        "occurred_at": occurred_at,
        "channel": channel,
        "summary": summary,
        "source_ref": source_ref,
        "logged_by": logged_by or actor(),
        "created_at": now_utc(),
        "created_by": actor(),
    })


def inbound_signals_for(conn: sqlite3.Connection, campaign_id: int) -> list[sqlite3.Row]:
    return query(conn, "SELECT * FROM inbound_signals WHERE campaign_id = ? ORDER BY occurred_at",
                 (campaign_id,))
