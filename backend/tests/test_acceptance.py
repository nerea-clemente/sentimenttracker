"""The spec's acceptance criteria, each verified end to end.

These duplicate coverage that exists elsewhere on purpose. They are written against the wording of
the acceptance criteria so that anyone can read this file and see whether the tool does what it
was asked to do, without knowing where the implementation lives.
"""

from __future__ import annotations

import pytest

from cib import dedup
from cib.db import query
from cib.export import briefing
from cib.ingest import csv_generic
from cib.metrics import METRIC_ORDER, PrePublicationError, campaign_metrics, compare
from cib.repo import campaigns as campaign_repo
from cib.repo import events as event_repo


@pytest.fixture
def two_campaigns(conn, sample_csv):
    """Two campaigns with imported data, published on the same day for a clean comparison."""
    ids = []
    for name, slug in (("Campaign A", "a"), ("Campaign B", "b")):
        cid = campaign_repo.create(
            conn, name=name, slug=slug, publisher_org="Publisher",
            campaign_type="ngo_report", status="archived",
            published_at="2019-03-04T00:00:00", timezone="Europe/Copenhagen",
        )
        csv_generic.import_file(conn, campaign_ref=cid, path=sample_csv,
                                default_tier="national_general")
        dedup.cluster_campaign(conn, cid)
        ids.append(cid)
    return ids


def test_criterion_1_identical_day_index_and_every_figure_drills_down(two_campaigns, conn):
    """"Two campaigns with imported data can be compared at an identical day index, and every
    figure drills down to source rows."""
    result = compare(conn, two_campaigns, at_day_index=5)
    assert result["at_day_index"] == 5
    assert len(result["campaigns"]) == 2

    for column in result["campaigns"]:
        assert column["at_day_index"] == 5
        for key in METRIC_ORDER:
            metric = column["metrics"][key]
            if not metric["available"]:
                assert metric["unavailable_reason"], f"{key} refuses without saying why"
                continue
            if not metric["row_ids"]:
                continue
            # The IDs must resolve to rows that actually exist in the named table.
            placeholders = ",".join("?" * len(metric["row_ids"]))
            found = conn.execute(
                f"SELECT COUNT(*) FROM {metric['row_table']} WHERE id IN ({placeholders})",
                tuple(metric["row_ids"]),
            ).fetchone()[0]
            assert found == len(set(metric["row_ids"])), f"{key} points at rows that do not exist"


def test_criterion_2_unique_stories_and_outlets_are_separate_and_the_ratio_is_right(
    two_campaigns, conn,
):
    """"Unique stories and unique outlets are reported separately, and the syndication ratio is
    correct on a hand-checked sample."

    The hand count for fixtures/syndication_sample.csv is documented in test_dedup.py:
    12 articles, 2 clusters covering 6 articles, 8 unique stories, 9 unique outlets, ratio 9/8.
    """
    result = campaign_metrics(conn, two_campaigns[0])
    assert result.value("unique_stories") == 8
    assert result.value("unique_outlets") == 9
    assert result.value("syndication_ratio") == pytest.approx(9 / 8, abs=0.001)

    # Verified independently of the metric code, straight from the rows.
    articles = query(conn, "SELECT cluster_id, outlet_id FROM articles WHERE campaign_id = ?",
                     (two_campaigns[0],))
    clusters = {r["cluster_id"] for r in articles if r["cluster_id"] is not None}
    singletons = sum(1 for r in articles if r["cluster_id"] is None)
    assert len(clusters) + singletons == 8
    assert len({r["outlet_id"] for r in articles}) == 9


def test_criterion_3_no_publication_date_means_no_footprint(conn, two_campaigns):
    """"A campaign with no publication date cannot render a footprint comparison; it renders
    publisher precedent and logged signals instead."""
    forthcoming = campaign_repo.create(
        conn, name="Forthcoming", publisher_org="Publisher", campaign_type="journalism",
        status="pre_publication", published_at=None,
    )
    campaign_repo.add_precedent(conn, forthcoming, two_campaigns[0], rationale="Same publisher.")
    event_repo.add_inbound_signal(
        conn, campaign_id=forthcoming, occurred_at="2019-02-01T00:00:00",
        channel="journalist", summary="Reporter requested comment.",
    )

    with pytest.raises(PrePublicationError):
        campaign_metrics(conn, forthcoming)

    result = compare(conn, [two_campaigns[0], forthcoming])
    assert [c["campaign_id"] for c in result["campaigns"]] == [two_campaigns[0]]
    assert len(result["pre_publication"]) == 1

    view = result["pre_publication"][0]
    assert view["footprint_available"] is False
    assert view["precedent_count"] == 1
    assert view["inbound_signal_count"] == 1
    assert view["publisher_precedent"][0]["footprint"]["unique_stories"]["value"] == 8


def test_criterion_4_no_metric_depends_on_an_unsourced_number(two_campaigns, conn):
    """"No metric anywhere in the system depends on an unsourced or model-generated number."""
    import sqlite3

    from cib.db import insert

    # The database itself rejects a reach figure with no named source.
    with pytest.raises(sqlite3.IntegrityError):
        insert(conn, "outlets", {"name": "X", "tier": "trade", "reach_value": 1,
                                 "created_at": "t", "created_by": "t"})

    # Every reach figure present in the database has a source.
    unsourced = query(
        conn, "SELECT COUNT(*) AS n FROM outlets WHERE reach_value IS NOT NULL "
              "AND reach_source IS NULL"
    )
    assert int(unsourced[0]["n"]) == 0

    # Every classification carries its evidence, and any LLM-assigned one carries a confidence.
    assert int(query(conn, "SELECT COUNT(*) AS n FROM mentions "
                           "WHERE trim(evidence_sentence) = ''")[0]["n"]) == 0
    assert int(query(conn, "SELECT COUNT(*) AS n FROM mentions "
                           "WHERE classified_by = 'llm' AND confidence IS NULL")[0]["n"]) == 0

    # Reach is reported as null with a stated gap rather than a fabricated total.
    reach = campaign_metrics(conn, two_campaigns[0]).get("total_reach")
    assert reach.value is None
    assert reach.basis["outlets_with_reach"] == 0
    assert any("floor, not a total" in c for c in reach.caveats)


def test_criterion_5_reimporting_the_same_export_creates_no_duplicates(conn, campaign_id,
                                                                      sample_csv):
    """"Re-importing the same export file does not create duplicate articles."""
    first = csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv)
    second = csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv)
    third = csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv)

    assert (first.inserted, second.inserted, third.inserted) == (12, 0, 0)
    assert int(query(conn, "SELECT COUNT(*) AS n FROM articles WHERE campaign_id = ?",
                     (campaign_id,))[0]["n"]) == 12
    # All three attempts are on the ledger: "nothing changed" is not "nobody checked".
    assert int(query(conn, "SELECT COUNT(*) AS n FROM imports")[0]["n"]) == 3


def test_criterion_6_the_briefing_can_be_defended_line_by_line(two_campaigns, conn):
    """"The briefing export can be handed to a non-technical stakeholder and defended line by
    line."

    Defensible means: every figure appears with what it counts, how many source rows produced it,
    its caveats, and an appendix of the underlying records with their import provenance.
    """
    event_repo.add_escalation(
        conn, campaign_id=two_campaigns[0], occurred_at="2019-03-20T00:00:00",
        escalation_type="regulatory_action", actor_name="Example Authority",
        description="Opened an inquiry.", source_url="https://authority.example/inquiry",
        severity=4,
    )
    data = briefing.build(conn, two_campaigns, at_day_index=10)
    markdown = briefing.to_markdown(data)

    assert "| Metric | Value | Source rows | What it counts |" in markdown
    assert "Data quality" in markdown
    assert "Evidence appendix" in markdown
    assert "syndication_sample.csv" in markdown            # import provenance
    assert "https://authority.example/inquiry" in markdown  # escalation source
    assert "Severity scale:" in markdown                    # the weighting is stated
    assert "Comparison at day 10" in markdown

    # Every metric shown carries the definition of what it counts.
    for key in METRIC_ORDER:
        metric = data["detail"]["metrics"]["metrics"].get(key)
        if metric:
            assert metric["definition"]["counts"], f"{key} appears without saying what it counts"

    html = briefing.to_html(data)
    assert html.startswith("<!doctype html>")
    assert "src=" not in html    # self-contained: renders with no network


def test_the_comparison_view_is_never_driven_by_sentiment(two_campaigns, conn):
    """Design principle 3, checked at the boundary rather than only in the source."""
    result = compare(conn, two_campaigns, at_day_index=5)
    serialised = repr(result).lower()
    for column in result["campaigns"]:
        assert not any("tone" in k or "sentiment" in k for k in column["metrics"])
    assert "article_tone" not in serialised
