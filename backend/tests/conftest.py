"""Test fixtures. Every test gets a fresh database built from the real migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from cib.actor import set_actor
from cib.db import connect
from cib.migrations.runner import migrate
from cib.repo import campaigns as campaign_repo

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _actor():
    set_actor("test:pytest")
    yield
    set_actor(None)


@pytest.fixture
def conn(tmp_path) -> sqlite3.Connection:
    connection = connect(tmp_path / "test.sqlite3")
    migrate(connection)
    yield connection
    connection.close()


@pytest.fixture
def campaign_id(conn) -> int:
    """An archived campaign published 2019-03-04, matching the syndication fixture's day 0."""
    return campaign_repo.create(
        conn,
        name="2019 fishmeal investigation (test)",
        publisher_org="Test Publisher",
        campaign_type="ngo_report",
        status="archived",
        published_at="2019-03-04T00:00:00",
        timezone="Europe/Copenhagen",
        themes=["fishmeal"],
    )


@pytest.fixture
def sample_csv() -> Path:
    return FIXTURES / "syndication_sample.csv"


@pytest.fixture
def loaded(conn, campaign_id, sample_csv):
    """The syndication fixture imported and clustered. Returns the campaign id."""
    from cib import dedup
    from cib.ingest import csv_generic

    csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv,
                            default_tier="national_general")
    dedup.cluster_campaign(conn, campaign_id)
    return campaign_id
