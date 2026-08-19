"""Metric behaviour, against the hand-counted fixture.

The fixture publishes on 2019-03-04 (day 0) and runs to 2019-04-08 (day 35). Per-day article
counts, hand-counted from fixtures/syndication_sample.csv:

    day 0: 2   day 1: 2   day 2: 1   day 3: 2   day 4: 1
    day 5: 1   day 6: 1   day 12: 1  day 35: 1
"""

from __future__ import annotations

import pytest

from cib.metrics import METRIC_ORDER, campaign_metrics
from cib.metrics import definitions as metric_definitions


def test_unique_stories_and_unique_outlets_are_reported_separately(loaded, conn):
    result = campaign_metrics(conn, loaded)
    assert result.value("unique_stories") == 8
    assert result.value("unique_outlets") == 9
    assert result.value("total_articles") == 12
    # The three are genuinely different numbers; conflating any two is the failure mode.
    assert len({result.value("unique_stories"), result.value("unique_outlets"),
                result.value("total_articles")}) == 3


def test_syndication_ratio_matches_the_hand_check(loaded, conn):
    result = campaign_metrics(conn, loaded)
    metric = result.get("syndication_ratio")
    assert metric.value == pytest.approx(9 / 8, abs=0.001)
    assert metric.basis["unique_outlets"] == 9
    assert metric.basis["unique_stories"] == 8


def test_every_metric_carries_the_rows_that_produced_it(loaded, conn):
    """Design principle #1: a value that cannot be traced must not be displayed."""
    result = campaign_metrics(conn, loaded)
    for key in METRIC_ORDER:
        metric = result.get(key)
        assert metric is not None, f"{key} missing from the metric set"
        if not metric.available:
            assert metric.unavailable_reason, f"{key} is unavailable without saying why"
            assert metric.value is None, f"{key} is unavailable but still carries a value"
            continue
        if metric.value in (None, 0, False) or key in ("reach_coverage", "total_reach"):
            continue
        assert metric.row_ids, f"{key} has a value but no source rows"
        assert metric.row_table, f"{key} has source rows but does not say which table"


def test_every_metric_has_a_written_definition(loaded, conn):
    """A metric nobody has written a definition for cannot be defended, so it cannot ship."""
    result = campaign_metrics(conn, loaded)
    for key in result.metrics:
        definition = metric_definitions.get(key)
        assert definition.plain and definition.counts


def test_row_ids_resolve_to_real_rows(loaded, conn):
    result = campaign_metrics(conn, loaded)
    for key, metric in result.metrics.items():
        if not metric.row_ids:
            continue
        placeholders = ",".join("?" * len(metric.row_ids))
        found = conn.execute(
            f"SELECT COUNT(*) FROM {metric.row_table} WHERE id IN ({placeholders})",
            tuple(metric.row_ids),
        ).fetchone()[0]
        assert found == len(set(metric.row_ids)), f"{key} points at rows that do not exist"


def test_day_index_alignment(loaded, conn):
    from cib.db import query

    counts = {int(r["day_index"]): int(r["n"]) for r in query(conn, """
        SELECT day_index, COUNT(*) AS n FROM articles WHERE campaign_id = ?
         GROUP BY day_index ORDER BY day_index
    """, (loaded,))}
    assert counts == {0: 2, 1: 2, 2: 1, 3: 2, 4: 1, 5: 1, 6: 1, 12: 1, 35: 1}


def test_peak_day_and_ties_resolve_to_the_earlier_day(loaded, conn):
    """Days 0, 1 and 3 all have two articles. The peak must be day 0, not day 3."""
    result = campaign_metrics(conn, loaded)
    assert result.value("peak_volume") == 2
    assert result.value("peak_day") == 0
    assert result.value("days_to_peak") == 0


def test_long_tail_flag(loaded, conn):
    result = campaign_metrics(conn, loaded)
    metric = result.get("long_tail")
    assert metric.value is True
    assert metric.basis["articles_after_day_30"] == 1


def test_days_to_90pct_refuses_for_a_live_campaign(conn, campaign_id, sample_csv):
    from cib import dedup
    from cib.ingest import csv_generic
    from cib.repo import campaigns as campaign_repo

    csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv)
    dedup.cluster_campaign(conn, campaign_id)
    campaign_repo.set_status(conn, campaign_id, "live")

    metric = campaign_metrics(conn, campaign_id).get("days_to_90pct")
    assert metric.available is False
    assert metric.value is None
    assert "not final" in metric.unavailable_reason


def test_days_to_90pct_computes_for_an_archived_campaign(loaded, conn):
    metric = campaign_metrics(conn, loaded).get("days_to_90pct")
    # Cumulative reaches 11 of 12 (>= 90% = 10.8) on day 12.
    assert metric.available is True
    assert metric.value == 12
    assert metric.basis["total_articles"] == 12


def test_reach_is_null_and_coverage_is_zero_when_nothing_is_sourced(loaded, conn):
    """No outlet has a sourced reach figure, so the sum must be null, never zero."""
    result = campaign_metrics(conn, loaded)
    reach = result.get("total_reach")
    assert reach.value is None
    assert reach.basis["outlets_with_reach"] == 0
    assert reach.basis["outlets_total"] == 9
    assert result.value("reach_coverage") == 0.0
    assert any("floor, not a total" in c for c in reach.caveats)


def test_reach_sum_and_coverage_travel_together(loaded, conn):
    from cib.db import query
    from cib.repo import outlets as outlet_repo

    outlet_id = int(query(conn, "SELECT id FROM outlets WHERE name = 'Nordic Daily'")[0]["id"])
    outlet_repo.set_reach(conn, outlet_id, reach_value=200_000,
                          reach_source="Danske Medier readership survey 2024")

    reach = campaign_metrics(conn, loaded).get("total_reach")
    assert reach.value == 200_000
    assert reach.basis["outlets_with_reach"] == 1
    assert reach.basis["coverage_pct"] == pytest.approx(11.1, abs=0.1)
    assert any("1 of 9 outlets" in c for c in reach.caveats)


def test_country_and_language_counts_with_the_unknown_share_shown(loaded, conn):
    result = campaign_metrics(conn, loaded)
    country = result.get("country_count")
    assert country.value == 5           # DK, GB, NO, NL, FR
    assert country.basis["articles_without_country"] == 0
    assert result.value("language_count") == 2   # en, fr


def test_tier_mix_shares_sum_to_one(loaded, conn):
    mix = campaign_metrics(conn, loaded).get("tier_mix")
    assert sum(entry["articles"] for entry in mix.value.values()) == 12
    assert sum(entry["share"] for entry in mix.value.values()) == pytest.approx(1.0, abs=0.001)
