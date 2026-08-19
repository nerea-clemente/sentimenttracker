"""Setup checks.

The point of `cib doctor` is that an unconfigured install does not look like a measured finding of
low coverage. These tests pin the gaps it must catch, and — just as importantly — that it goes
quiet once they are fixed.
"""

from __future__ import annotations

import pytest

from cib import dedup, doctor
from cib.ingest import csv_generic
from cib.repo import campaigns as campaign_repo
from cib.repo import entities as entity_repo
from cib.repo import events as event_repo
from cib.repo import outlets as outlet_repo
from cib.watch import rules as rule_repo


def codes(report, level=None) -> set[str]:
    return {f.code for f in (report.of(level) if level else report.findings)}


def test_a_fresh_seed_is_all_blockers_and_says_why(conn):
    from cib import seed

    seed.run(conn)
    report = doctor.run(conn)

    assert codes(report, "blocker") >= {
        "placeholder-campaigns",     # TODO names
        "placeholder-dates",         # stand-in publication dates
        "no-coverage",               # archived campaigns with nothing imported
        "unwatched-prepublication",  # every rule disabled
        "placeholder-own-company",   # TODO — our company
        "nothing-imported",
    }
    assert codes(report, "warning") >= {"no-competitors", "no-automated-ingest"}
    # Every finding must be actionable, not just a complaint.
    for finding in report.blockers:
        assert finding.fix, f"{finding.code} reports a problem with no fix"


def test_an_empty_database_reports_no_campaigns(conn):
    report = doctor.run(conn)
    assert "no-campaigns" in codes(report, "blocker")


def test_a_placeholder_publication_date_is_a_blocker(conn):
    """Every day_index measures from published_at, so a stand-in date poisons every comparison."""
    campaign_repo.create(conn, name="Real campaign", slug="real", publisher_org="Real publisher",
                         campaign_type="ngo_report", status="archived",
                         published_at="2019-01-01T00:00:00")
    assert "placeholder-dates" in codes(doctor.run(conn), "blocker")

    campaign_repo.set_published(conn, campaign_repo.get_by_slug(conn, "real").id,
                                "2019-03-04T00:00:00", status="archived")
    assert "placeholder-dates" not in codes(doctor.run(conn))


def test_own_company_states_are_distinguished(conn):
    assert "no-own-company" in codes(doctor.run(conn), "blocker")

    entity_repo.create(conn, name="TODO — our company", type="own_company")
    assert "placeholder-own-company" in codes(doctor.run(conn), "blocker")

    conn.execute("DELETE FROM entities")
    entity_repo.create(conn, name="Nordisk Aqua Feed", type="own_company")
    assert "own-company-no-aliases" in codes(doctor.run(conn), "warning")

    conn.execute("DELETE FROM entities")
    entity_repo.create(conn, name="Nordisk Aqua Feed", type="own_company",
                       aliases=["Nordisk Aqua Feed A/S"])
    report = doctor.run(conn)
    assert "own-company" in codes(report, "ok")

    entity_repo.create(conn, name="Second company", type="own_company")
    assert "ambiguous-own-company" in codes(doctor.run(conn), "blocker")


def test_an_enabled_rule_pointing_at_a_placeholder_is_a_blocker(conn):
    """A disabled placeholder is untidy; an enabled one is a rule that can never fire."""
    rule_id = rule_repo.create(conn, name="Feed", rule_type="rss",
                               pattern="https://example.invalid/feed", enabled=False)
    assert "placeholder-watch-patterns" in codes(doctor.run(conn), "warning")

    rule_repo.set_enabled(conn, rule_id, True)
    assert "placeholder-watch-patterns" in codes(doctor.run(conn), "blocker")


def test_a_watched_pre_publication_campaign_stops_being_flagged(conn):
    campaign = campaign_repo.create(
        conn, name="Forthcoming", slug="soon", publisher_org="Publisher",
        campaign_type="journalism", status="pre_publication", published_at=None,
    )
    rule_id = rule_repo.create(conn, name="Publisher feed", rule_type="rss",
                               pattern="https://publisher.example/feed",
                               campaign_id=campaign, enabled=False)
    assert "unwatched-prepublication" in codes(doctor.run(conn), "blocker")

    rule_repo.set_enabled(conn, rule_id, True)
    assert "unwatched-prepublication" not in codes(doctor.run(conn))


def test_missing_automated_ingest_is_flagged(conn):
    """Detection and ingestion are different jobs; having feeds does not mean coverage flows in."""
    campaign = campaign_repo.create(
        conn, name="Live", slug="live", publisher_org="P", campaign_type="journalism",
        status="live", published_at="2019-03-04T00:00:00",
    )
    # With no rules at all the broader finding covers both jobs.
    assert "no-watch-rules" in codes(doctor.run(conn), "warning")

    # A detection-only feed still leaves nothing importing coverage.
    rule_repo.create(conn, name="Publisher feed", rule_type="rss",
                     pattern="https://publisher.example/feed", campaign_id=campaign)
    assert "no-automated-ingest" in codes(doctor.run(conn), "warning")

    rule_repo.create(conn, name="GDELT", rule_type="gdelt_query", pattern="fishmeal",
                     campaign_id=campaign)
    assert "no-automated-ingest" not in codes(doctor.run(conn))


def test_reach_coverage_is_graded(conn, campaign_id, sample_csv):
    csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv)
    dedup.cluster_campaign(conn, campaign_id)
    assert "no-reach" in codes(doctor.run(conn), "warning")

    for outlet in outlet_repo.list_all(conn):
        outlet_repo.set_reach(conn, outlet.id, reach_value=1000, reach_source="survey")
    report = doctor.run(conn)
    assert "reach" in codes(report, "ok")
    assert "no-reach" not in codes(report)


def test_alert_channel_states(conn, monkeypatch):
    from cib import config

    monkeypatch.setattr(config, "get", lambda name, default="": {
        "CIB_NOTIFY_CHANNELS": "stdout"}.get(name, default))
    assert "alerts-stdout-only" in codes(doctor.run(conn), "warning")

    # Configured but unusable is worse than the default: it looks monitored and is not.
    monkeypatch.setattr(config, "get", lambda name, default="": {
        "CIB_NOTIFY_CHANNELS": "webhook"}.get(name, default))
    assert "alerts-misconfigured" in codes(doctor.run(conn), "blocker")

    monkeypatch.setattr(config, "get", lambda name, default="": {
        "CIB_NOTIFY_CHANNELS": "webhook",
        "CIB_NOTIFY_WEBHOOK_URL": "https://hooks.example/x"}.get(name, default))
    assert "alerts" in codes(doctor.run(conn), "ok")


def test_placeholder_inbound_signal_dates_are_flagged(conn, campaign_id):
    event_repo.add_inbound_signal(conn, campaign_id=campaign_id,
                                  occurred_at="1970-01-01T00:00:00", channel="journalist",
                                  summary="TODO — congressional testimony")
    assert "placeholder-signals" in codes(doctor.run(conn), "warning")


def test_a_fully_configured_install_has_no_blockers(conn, sample_csv):
    """The checks have to be satisfiable, or they are noise rather than a checklist."""
    from cib import config

    campaign = campaign_repo.create(
        conn, name="2019 West Africa fishmeal investigation", slug="c2019",
        publisher_org="Example NGO", campaign_type="ngo_report", status="archived",
        published_at="2019-03-04T00:00:00",
    )
    csv_generic.import_file(conn, campaign_ref=campaign, path=sample_csv,
                            default_tier="national_general")
    dedup.cluster_campaign(conn, campaign)
    entity_repo.create(conn, name="Nordisk Aqua Feed", type="own_company",
                       aliases=["Nordisk Aqua Feed A/S"])
    entity_repo.create(conn, name="Rival Feeds", type="competitor")
    rule_repo.create(conn, name="GDELT", rule_type="gdelt_query",
                     pattern='"fishmeal"', campaign_id=campaign)
    event_repo.add_escalation(
        conn, campaign_id=campaign, occurred_at="2019-03-20T00:00:00",
        escalation_type="regulatory_action", actor_name="Authority",
        description="Inquiry opened.", source_url="https://authority.example/x", severity=4,
    )
    for outlet in outlet_repo.list_all(conn):
        outlet_repo.set_reach(conn, outlet.id, reach_value=1000, reach_source="survey")
    from cib.watch import poller
    poller.poll_once(conn, notify=False)

    import pytest as _pytest
    monkeypatch = _pytest.MonkeyPatch()
    monkeypatch.setattr(config, "get", lambda name, default="": {
        "CIB_NOTIFY_CHANNELS": "webhook",
        "CIB_NOTIFY_WEBHOOK_URL": "https://hooks.example/x"}.get(name, default))
    try:
        report = doctor.run(conn)
    finally:
        monkeypatch.undo()

    assert report.blockers == [], [f.title for f in report.blockers]
    assert codes(report, "ok") >= {"own-company", "poller", "reach", "alerts"}


@pytest.mark.parametrize("value,expected", [
    ("TODO — our company", True),
    ("https://example.invalid/feed", True),
    ("TODO REPLACE WITH BYLINE", True),
    ("Nordisk Aqua Feed", False),
    (None, False),
])
def test_placeholder_detection(value, expected):
    assert doctor._looks_placeholder(value) is expected
