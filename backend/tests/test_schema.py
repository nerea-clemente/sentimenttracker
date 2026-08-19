"""The design principles are enforced by the schema, so they are tested against the schema."""

from __future__ import annotations

import sqlite3

import pytest

from cib.actor import now_utc
from cib.db import insert
from cib.repo import campaigns as campaign_repo
from cib.repo import entities as entity_repo
from cib.repo import events as event_repo
from cib.repo import outlets as outlet_repo


def _outlet(**overrides) -> dict:
    return {
        "name": "Test Outlet", "tier": "trade",
        "created_at": now_utc(), "created_by": "test", **overrides,
    }


def test_reach_value_requires_a_source(conn):
    """Design principle: no invented data. An unsourced reach figure is rejected by the database."""
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, "outlets", _outlet(reach_value=250_000))


def test_reach_value_with_a_source_is_accepted(conn):
    outlet_id = insert(conn, "outlets", _outlet(
        reach_value=250_000, reach_source="Danske Medier readership survey 2024",
    ))
    assert outlet_repo.get(conn, outlet_id).reach_value == 250_000


def test_estimated_reach_requires_a_method(conn):
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, "outlets", _outlet(
            reach_value=100, reach_source="internal", reach_is_estimated=1,
        ))


def test_set_reach_refuses_an_empty_source(conn):
    outlet_id = outlet_repo.get_or_create(conn, name="Example", tier="trade")
    with pytest.raises(ValueError, match="reach_source is required"):
        outlet_repo.set_reach(conn, outlet_id, reach_value=1000, reach_source="  ")


def test_set_reach_refuses_an_estimate_without_a_method(conn):
    outlet_id = outlet_repo.get_or_create(conn, name="Example", tier="trade")
    with pytest.raises(ValueError, match="estimation_method is required"):
        outlet_repo.set_reach(conn, outlet_id, reach_value=1000, reach_source="analyst note",
                              is_estimated=True)


def test_pre_publication_status_and_published_at_must_agree(conn):
    """A campaign is pre_publication if and only if it has no publication date."""
    with pytest.raises(sqlite3.IntegrityError):
        campaign_repo.create(conn, name="Bad", publisher_org="X", campaign_type="journalism",
                             status="pre_publication", published_at="2024-01-01T00:00:00")
    with pytest.raises(sqlite3.IntegrityError):
        campaign_repo.create(conn, name="Bad2", publisher_org="X", campaign_type="journalism",
                             status="live", published_at=None)


def test_escalation_requires_a_source_url(conn, campaign_id):
    with pytest.raises(ValueError, match="source_url is required"):
        event_repo.add_escalation(
            conn, campaign_id=campaign_id, occurred_at="2019-03-10T00:00:00",
            escalation_type="regulatory_action", actor_name="Authority",
            description="Opened an inquiry", source_url="", severity=4,
        )


def test_escalation_severity_is_bounded(conn, campaign_id):
    with pytest.raises(ValueError, match="severity must be one of"):
        event_repo.add_escalation(
            conn, campaign_id=campaign_id, occurred_at="2019-03-10T00:00:00",
            escalation_type="other", actor_name="X", description="Y",
            source_url="https://example.invalid/a", severity=9,
        )


def test_mention_requires_evidence(conn, campaign_id):
    entity_id = entity_repo.create(conn, name="Acme Feed", type="own_company")
    with pytest.raises(ValueError, match="evidence_sentence is required"):
        entity_repo.add_mention(conn, article_id=1, entity_id=entity_id,
                                role="subject", evidence_sentence="   ")


def test_llm_mention_requires_a_confidence(conn, campaign_id):
    entity_id = entity_repo.create(conn, name="Acme Feed", type="own_company")
    with pytest.raises(ValueError, match="confidence"):
        entity_repo.add_mention(conn, article_id=1, entity_id=entity_id, role="subject",
                                evidence_sentence="Acme Feed was named.", classified_by="llm")


def test_every_table_records_who_wrote_it(conn):
    """Convention: every write path records who or what wrote it, and when."""
    tables = [r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )]
    exempt = {"metrics_daily", "watch_hits", "schema_migrations", "cluster_members"}
    for table in tables:
        if table in exempt:
            continue
        columns = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        assert "created_by" in columns or "applied_by" in columns, f"{table} has no author column"
