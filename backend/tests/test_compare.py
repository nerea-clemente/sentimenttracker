"""Comparison behaviour.

The single most important property under test: two campaigns truncated at the same day index
produce figures covering the same window, so a live campaign at day 5 is never measured against
another campaign's lifetime.
"""

from __future__ import annotations

import pytest

from cib import dedup
from cib.ingest import csv_generic
from cib.metrics import PrePublicationError, campaign_metrics, compare, prepublication_view
from cib.repo import campaigns as campaign_repo


@pytest.fixture
def second_campaign(conn, sample_csv):
    """A second campaign carrying the same fixture, published a year later.

    Same coverage shape, different calendar dates — which is exactly the case day-index alignment
    exists to handle.
    """
    campaign_id = campaign_repo.create(
        conn, name="2020 coalition campaign (test)", publisher_org="Other Publisher",
        campaign_type="coalition", status="archived",
        published_at="2019-03-04T00:00:00", timezone="Europe/Copenhagen",
    )
    csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv)
    dedup.cluster_campaign(conn, campaign_id)
    return campaign_id


def test_truncation_at_a_day_index_narrows_every_metric(loaded, conn):
    lifetime = campaign_metrics(conn, loaded, at_day_index=None)
    day_two = campaign_metrics(conn, loaded, at_day_index=2)

    assert lifetime.value("total_articles") == 12
    assert day_two.value("total_articles") == 5      # days 0, 1 and 2
    assert day_two.value("unique_outlets") == 5
    assert day_two.value("long_tail") is False


def test_the_same_cutoff_is_applied_to_every_campaign(loaded, second_campaign, conn):
    result = compare(conn, [loaded, second_campaign], at_day_index=3)
    assert result["at_day_index"] == 3
    assert result["cutoff_was_explicit"] is True
    assert len(result["campaigns"]) == 2

    totals = {c["campaign_slug"]: c["metrics"]["total_articles"]["value"]
              for c in result["campaigns"]}
    assert set(totals.values()) == {7}, "identical coverage truncated identically must match"


def test_an_omitted_cutoff_uses_the_shortest_window_and_says_so(loaded, conn, sample_csv):
    """Comparing five days against five years is the failure this guard exists to prevent."""
    short_id = campaign_repo.create(
        conn, name="Short campaign", publisher_org="P", campaign_type="journalism",
        status="archived", published_at="2019-03-04T00:00:00",
    )
    csv_generic.import_file(conn, campaign_ref=short_id, path=sample_csv)
    conn.execute("DELETE FROM articles WHERE campaign_id = ? AND day_index > 2", (short_id,))

    result = compare(conn, [loaded, short_id])
    assert result["at_day_index"] == 2
    assert result["cutoff_was_explicit"] is False
    assert any("shortest observed window" in c for c in result["caveats"])
    assert any("lifetime totals are larger" in c for c in result["caveats"])


def test_comparison_needs_at_least_two_campaigns(loaded, conn):
    with pytest.raises(ValueError, match="at least two"):
        compare(conn, [loaded])


def test_comparison_is_capped_at_four(loaded, conn):
    with pytest.raises(ValueError, match="four campaigns"):
        compare(conn, [loaded] * 5)


def test_every_comparison_cell_carries_its_row_ids(loaded, second_campaign, conn):
    result = compare(conn, [loaded, second_campaign], at_day_index=5)
    for column in result["campaigns"]:
        for key, metric in column["metrics"].items():
            assert "row_ids" in metric and "row_table" in metric, key
            assert metric["row_count"] == len(metric["row_ids"])


def test_a_campaign_short_of_the_cutoff_is_flagged_not_silently_compared(loaded, conn, sample_csv):
    short_id = campaign_repo.create(
        conn, name="Short campaign", publisher_org="P", campaign_type="journalism",
        status="archived", published_at="2019-03-04T00:00:00",
    )
    csv_generic.import_file(conn, campaign_ref=short_id, path=sample_csv)
    conn.execute("DELETE FROM articles WHERE campaign_id = ? AND day_index > 4", (short_id,))

    result = compare(conn, [loaded, short_id], at_day_index=30)
    assert any("short of the day 30 cutoff" in c for c in result["caveats"])


# --------------------------------------------------------------------- pre-publication

@pytest.fixture
def prepub_campaign(conn, loaded):
    campaign_id = campaign_repo.create(
        conn, name="Forthcoming investigation (test)", publisher_org="Test Publisher",
        campaign_type="journalism", status="pre_publication", published_at=None,
        first_signal_at="2019-02-01T00:00:00",
    )
    campaign_repo.add_precedent(conn, campaign_id, loaded,
                                rationale="Same publisher, same sector.")
    from cib.repo import events as event_repo
    event_repo.add_inbound_signal(
        conn, campaign_id=campaign_id, occurred_at="2019-02-10T00:00:00",
        channel="journalist", summary="Reporter requested comment on sourcing.",
    )
    return campaign_id


def test_a_campaign_with_no_publication_date_cannot_render_a_footprint(prepub_campaign, conn):
    """Acceptance criterion: '0 articles' must never be readable as 'low risk'."""
    with pytest.raises(PrePublicationError, match="no publication date"):
        campaign_metrics(conn, prepub_campaign)


def test_the_pre_publication_view_shows_precedent_and_signals_instead(prepub_campaign, conn):
    view = prepublication_view(conn, prepub_campaign)
    assert view["footprint_available"] is False
    assert "low risk" in view["footprint_refusal"]
    assert view["precedent_count"] == 1
    assert view["inbound_signal_count"] == 1

    precedent = view["publisher_precedent"][0]
    assert precedent["footprint"]["unique_stories"]["value"] == 8
    assert precedent["footprint"]["unique_outlets"]["value"] == 9
    assert precedent["rationale"]


def test_the_pre_publication_view_contains_no_footprint_numbers_for_itself(prepub_campaign, conn):
    view = prepublication_view(conn, prepub_campaign)
    assert "metrics" not in view
    for key in ("unique_stories", "unique_outlets", "total_articles"):
        assert key not in view


def test_compare_separates_pre_publication_campaigns_from_the_table(prepub_campaign, loaded, conn):
    result = compare(conn, [loaded, prepub_campaign])
    assert [c["campaign_slug"] for c in result["campaigns"]] == [
        campaign_repo.get(conn, loaded).slug
    ]
    assert len(result["pre_publication"]) == 1
    assert any("excluded from the footprint table by design" in c for c in result["caveats"])


def test_pre_publication_view_refuses_a_published_campaign(loaded, conn):
    with pytest.raises(ValueError, match="has published"):
        prepublication_view(conn, loaded)


def test_promoting_a_campaign_reindexes_its_day_axis(prepub_campaign, conn, sample_csv):
    """Coverage imported before publication must be re-placed on the axis, not left stranded."""
    from cib.repo import articles as article_repo

    csv_generic.import_file(conn, campaign_ref=prepub_campaign, path=sample_csv)
    stranded = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE campaign_id = ? AND day_index IS NULL",
        (prepub_campaign,),
    ).fetchone()[0]
    assert stranded == 12, "with no publication date there is no day axis to place them on"

    campaign_repo.set_published(conn, prepub_campaign, "2019-03-06T00:00:00")
    article_repo.recompute_day_index(conn, prepub_campaign, "2019-03-06T00:00:00",
                                     "Europe/Copenhagen")

    result = campaign_metrics(conn, prepub_campaign)
    assert result.value("total_articles") == 12
    # Published two days after the fixture's first coverage, so day 0 coverage becomes day -2.
    assert result.value("peak_day") == -2


def test_an_unmeasured_precedent_is_flagged_not_shown_as_zeros(conn):
    """A precedent with nothing imported must not read as "that investigation went nowhere"."""
    empty = campaign_repo.create(
        conn, name="Past investigation, not yet imported", publisher_org="Test Publisher",
        campaign_type="ngo_report", status="archived", published_at="2019-03-04T00:00:00",
    )
    forthcoming = campaign_repo.create(
        conn, name="Forthcoming", publisher_org="Test Publisher", campaign_type="journalism",
        status="pre_publication", published_at=None,
    )
    campaign_repo.add_precedent(conn, forthcoming, empty, rationale="Same publisher.")

    precedent = prepublication_view(conn, forthcoming)["publisher_precedent"][0]
    assert precedent["has_measured_footprint"] is False
    assert any("empty dataset, not a measurement of low coverage" in c
               for c in precedent["caveats"])


def test_a_measured_precedent_reports_its_footprint(conn, loaded):
    forthcoming = campaign_repo.create(
        conn, name="Forthcoming", publisher_org="Test Publisher", campaign_type="journalism",
        status="pre_publication", published_at=None,
    )
    campaign_repo.add_precedent(conn, forthcoming, loaded, rationale="Same publisher.")

    precedent = prepublication_view(conn, forthcoming)["publisher_precedent"][0]
    assert precedent["has_measured_footprint"] is True
    assert precedent["footprint"]["unique_stories"]["value"] == 8
