"""The seed carries real identities, so it has to meet the same standard as the rest of the tool.

Design principle 2 is "no invented data". Seed records are the one place where a fact could be
written from recollection and never questioned again, so these tests hold them to the rule: every
identity cites a source, and anything not confirmed is marked rather than passed off as known.
"""

from __future__ import annotations

import re

import pytest

from cib import doctor, seed
from cib.metrics import campaign_metrics, prepublication_view
from cib.repo import campaigns as campaign_repo
from cib.repo import entities as entity_repo
from cib.watch import rules as rule_repo

URL = re.compile(r"https?://\S+")


@pytest.fixture
def seeded(conn):
    seed.run(conn)
    return conn


def test_every_seeded_campaign_cites_a_source(seeded, conn):
    for campaign in campaign_repo.list_all(conn):
        assert campaign.notes, f"{campaign.slug} has no notes"
        assert URL.search(campaign.notes), (
            f"{campaign.slug} names no source. A campaign identity nobody can check is exactly "
            "the invented data this tool exists to prevent."
        )


def test_no_seeded_campaign_still_carries_a_placeholder_identity(seeded, conn):
    for campaign in campaign_repo.list_all(conn):
        assert "TODO" not in campaign.name
        assert "TODO" not in campaign.publisher_org


def test_dates_that_are_not_known_to_the_day_say_so(seeded, conn):
    """A guessed day would shift a campaign's whole timeline silently."""
    by_slug = {c.slug: c for c in campaign_repo.list_all(conn)}

    # Confirmed to the day.
    exact = by_slug["changing-markets-2019-fishing-for-catastrophe"]
    assert exact.published_at.startswith("2019-10-15")
    assert exact.published_at_precision == "day"
    assert exact.date_is_exact

    # Confirmed only to the month, and marked as such.
    month = by_slug["danwatch-2019-west-african-fishmeal"]
    assert month.published_at.startswith("2019-10")
    assert month.published_at_precision == "month"
    assert not month.date_is_exact
    assert "DATE PRECISION" in month.notes


def test_an_imprecise_date_caveats_every_metric_set(seeded, conn):
    """The error bar has to travel with the figures, not sit in a notes field."""
    result = campaign_metrics(conn, "danwatch-2019-west-african-fishmeal")
    assert any("only known to the month" in c for c in result.caveats)
    assert any("days to peak" in c for c in result.caveats)

    exact = campaign_metrics(conn, "changing-markets-2019-fishing-for-catastrophe")
    assert not any("only known to the" in c for c in exact.caveats)


def test_the_forthcoming_investigation_has_no_footprint_and_says_why(seeded, conn):
    view = prepublication_view(conn, "outlaw-ocean-food-for-feed")

    assert view["footprint_available"] is False
    assert view["precedent_count"] == 3
    assert view["inbound_signal_count"] == 2
    # The spec's two pre-publication signals: the testimony and its trade-press pickup.
    summaries = " ".join(s["summary"] for s in view["inbound_signals"])
    assert "Congressional testimony" in summaries
    assert "Trade-press pickup" in summaries
    for signal in view["inbound_signals"]:
        assert URL.search(signal["source_ref"] or ""), "a signal with no reference cannot be cited"


def test_unconfirmed_facts_are_flagged_for_verification(seeded, conn):
    """Where a fact could not be established, the record says so instead of implying certainty."""
    forthcoming = campaign_repo.get_by_slug(conn, "outlaw-ocean-food-for-feed")
    assert "VERIFY" in forthcoming.notes

    view = prepublication_view(conn, "outlaw-ocean-food-for-feed")
    assumed = [s for s in view["inbound_signals"] if "VERIFY" in s["summary"]]
    assert assumed, "the assumed signal dates must be marked for checking"


def test_exactly_one_own_company_with_aliases(seeded, conn):
    """Two own_company entities make every exposure metric refuse rather than measure."""
    candidates = entity_repo.own_company_candidates(conn)
    assert len(candidates) == 1
    biomar = candidates[0]
    assert biomar.name == "BioMar"
    assert "BioMar Group" in biomar.alias_list
    # The parent company must not be typed own_company, or exposure metrics go ambiguous.
    schouw = entity_repo.get_by_name(conn, "Schouw & Co")
    assert schouw is not None and schouw.type != "own_company"


def test_every_seeded_entity_says_where_it_came_from(seeded, conn):
    for entity in entity_repo.list_all(conn):
        assert entity.notes, f"{entity.name} has no notes"


def test_competitors_exist_so_the_peer_comparison_has_something_to_compare(seeded, conn):
    names = {e.name for e in entity_repo.list_all(conn, type="competitor")}
    assert {"Skretting", "Cargill Aqua Nutrition"} <= names


def test_a_rule_that_can_set_day_zero_is_never_enabled_on_an_unconfirmed_url(seeded, conn):
    for rule in rule_repo.list_all(conn):
        if rule.promotes_to_live:
            assert not rule.enabled, (
                f"rule #{rule.id} may rewrite the campaign's timeline but its URL is unconfirmed"
            )


def test_seeding_is_idempotent(conn):
    first = seed.run(conn)
    second = seed.run(conn)
    assert first["campaigns"] and not second["campaigns"]
    assert second["signals"] == 0 and second["watch_rules"] == 0 and second["precedents"] == 0


def test_the_seed_invents_no_coverage(seeded, conn):
    """The campaigns are real; their article data is not, and must not be."""
    from cib.db import query_one

    assert int(query_one(conn, "SELECT COUNT(*) AS n FROM articles")["n"]) == 0
    report = doctor.run(conn)
    assert "nothing-imported" in {f.code for f in report.blockers}
