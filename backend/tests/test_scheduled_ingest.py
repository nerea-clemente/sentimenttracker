"""Automated ingestion driven by watch rules.

Detection and measurement are different jobs: `cib watch poll` records that something published,
`cib ingest` brings the coverage in. These tests cover the second, and pin the separation.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from cib.db import query
from cib.ingest import gdelt, scheduled
from cib.ingest.http import FetchError
from cib.repo import campaigns as campaign_repo
from cib.watch import poller
from cib.watch import rules as rule_repo

# Mirrors a real GDELT DOC 2.0 ArtList response: `sourcecountry` is a country *name* and
# `language` a language name, not the ISO codes the rest of this tool stores.
GDELT_ARTICLES = [
    {"title": "Fishmeal investigation lands", "seendate": "20190304T060000Z",
     "url": "https://outlet-a.example/story", "domain": "outlet-a.example",
     "sourcecountry": "Denmark", "language": "English"},
    {"title": "Feed sector responds", "seendate": "20190305T070000Z",
     "url": "https://outlet-b.example/story", "domain": "outlet-b.example",
     "sourcecountry": "Norway", "language": "English"},
]


@pytest.fixture
def campaign(conn):
    return campaign_repo.create(
        conn, name="Live campaign", slug="live", publisher_org="P",
        campaign_type="journalism", status="live", published_at="2019-03-04T00:00:00",
    )


@pytest.fixture
def gdelt_rule(conn, campaign):
    return rule_repo.create(
        conn, name="GDELT: fishmeal", rule_type="gdelt_query",
        pattern='"fishmeal" "aquaculture"', campaign_id=campaign,
    )


def _stub_search(monkeypatch, articles=GDELT_ARTICLES, calls=None):
    def search(query_string, start=None, end=None, max_records=250, timeout=30):
        if calls is not None:
            calls.append({"query": query_string, "start": start, "end": end})
        return articles
    monkeypatch.setattr(gdelt, "search", search)


def test_ingest_imports_gdelt_coverage_into_the_rules_campaign(conn, gdelt_rule, monkeypatch):
    _stub_search(monkeypatch)
    report = scheduled.run(conn)

    assert len(report.results) == 1
    result = report.results[0]
    assert result.source == "gdelt"
    assert result.campaign_slug == "live"
    assert result.inserted == 2
    assert result.error is None

    rows = query(conn, """
        SELECT a.headline, a.day_index, o.name AS outlet, i.source
          FROM articles a JOIN outlets o ON o.id = a.outlet_id
          JOIN imports i ON i.id = a.import_id ORDER BY a.published_at
    """)
    assert [r["outlet"] for r in rows] == ["outlet-a.example", "outlet-b.example"]
    assert [r["day_index"] for r in rows] == [0, 1]
    assert {r["source"] for r in rows} == {"gdelt"}


def test_a_second_run_imports_nothing_new(conn, gdelt_rule, monkeypatch):
    """Windows overlap deliberately, so the dedup key has to carry the weight."""
    _stub_search(monkeypatch)
    first = scheduled.run(conn)
    second = scheduled.run(conn)

    assert first.results[0].inserted == 2
    assert second.results[0].inserted == 0
    assert second.results[0].duplicates == 2
    assert int(query(conn, "SELECT COUNT(*) AS n FROM articles")[0]["n"]) == 2


def test_the_window_advances_from_the_rules_watermark(conn, gdelt_rule, monkeypatch):
    calls: list[dict] = []
    _stub_search(monkeypatch, calls=calls)

    scheduled.run(conn, lookback_days=7)
    scheduled.run(conn, lookback_days=7)

    first_start = datetime.strptime(calls[0]["start"], "%Y%m%d%H%M%S")
    second_start = datetime.strptime(calls[1]["start"], "%Y%m%d%H%M%S")
    # First run reaches back a week; the second starts from the watermark minus the overlap.
    assert datetime.utcnow() - first_start >= timedelta(days=6, hours=23)
    assert second_start > first_start
    assert datetime.utcnow() - second_start <= timedelta(hours=scheduled.OVERLAP_HOURS + 1)


def test_a_dormant_rule_does_not_ask_for_an_unbounded_window(conn, gdelt_rule, monkeypatch):
    """A rule idle for a year must not request a year and get an arbitrary 250 articles."""
    calls: list[dict] = []
    _stub_search(monkeypatch, calls=calls)
    conn.execute("UPDATE watch_rules SET last_polled_at = ? WHERE id = ?",
                 ("2019-01-01T00:00:00Z", gdelt_rule))

    scheduled.run(conn, lookback_days=7)
    start = datetime.strptime(calls[0]["start"], "%Y%m%d%H%M%S")
    assert datetime.utcnow() - start <= timedelta(days=8)


def test_hitting_the_record_cap_is_reported_not_swallowed(conn, gdelt_rule, monkeypatch):
    """A capped response means coverage was dropped; silence would overstate completeness."""
    many = [
        {"title": f"Story {i}", "seendate": "20190304T060000Z",
         "url": f"https://outlet-{i}.example/s", "domain": f"outlet-{i}.example",
         "sourcecountry": "Denmark", "language": "English"}
        for i in range(scheduled.GDELT_MAX_RECORDS)
    ]
    _stub_search(monkeypatch, articles=many)
    report = scheduled.run(conn)
    assert report.results[0].hit_record_cap is True


def test_a_failed_fetch_does_not_advance_the_watermark(conn, gdelt_rule, monkeypatch):
    """Otherwise a transient outage leaves a permanent hole in the coverage."""
    def boom(*a, **k):
        raise FetchError("GDELT unreachable")
    monkeypatch.setattr(gdelt, "search", boom)

    report = scheduled.run(conn)
    assert report.errors == 1
    assert "unreachable" in report.results[0].error

    rule = rule_repo.get(conn, gdelt_rule)
    assert rule.last_error and "unreachable" in rule.last_error

    # Watermark still unset, so the next run re-fetches the same window.
    calls: list[dict] = []
    _stub_search(monkeypatch, calls=calls)
    scheduled.run(conn, lookback_days=7)
    start = datetime.strptime(calls[0]["start"], "%Y%m%d%H%M%S")
    assert datetime.utcnow() - start >= timedelta(days=6, hours=23)


def test_a_disabled_rule_is_not_ingested(conn, gdelt_rule, monkeypatch):
    _stub_search(monkeypatch)
    rule_repo.set_enabled(conn, gdelt_rule, False)
    assert scheduled.run(conn).results == []


def test_a_rule_with_no_campaign_is_skipped_with_a_reason(conn, monkeypatch):
    _stub_search(monkeypatch)
    rule_repo.create(conn, name="Orphan", rule_type="gdelt_query", pattern="fishmeal")
    report = scheduled.run(conn)
    assert report.results == []
    assert any("no campaign" in s for s in report.skipped)


def test_feed_rules_are_detection_only_unless_they_opt_in(conn, campaign, monkeypatch):
    """Importing a whole publisher feed would bury a campaign in unrelated articles."""
    detect_only = rule_repo.create(
        conn, name="Publisher feed", rule_type="rss",
        pattern="https://publisher.example/feed", campaign_id=campaign,
    )
    assert scheduled.run(conn).results == []

    conn.execute("UPDATE watch_rules SET notes = ? WHERE id = ?",
                 ("Trade press wire — ingest as coverage.", detect_only))

    from cib.ingest import feeds
    monkeypatch.setattr(feeds, "fetch", lambda url, timeout=30: [])
    results = scheduled.run(conn).results
    assert len(results) == 1
    assert results[0].source == "rss"


def test_new_coverage_is_reclustered(conn, gdelt_rule, monkeypatch):
    """Republications of an already-imported story must not inflate unique-story counts."""
    same_story = [
        {"title": "Identical wire headline about fishmeal sourcing", "seendate": "20190304T060000Z",
         "url": f"https://outlet-{i}.example/s", "domain": f"outlet-{i}.example",
         "sourcecountry": "Denmark", "language": "English"}
        for i in range(4)
    ]
    _stub_search(monkeypatch, articles=same_story)
    report = scheduled.run(conn)
    clustered = scheduled.cluster_and_reindex(conn, report)

    assert clustered == {"live": 1}
    from cib.metrics import campaign_metrics
    result = campaign_metrics(conn, "live")
    assert result.value("total_articles") == 4
    assert result.value("unique_stories") == 1
    assert result.value("unique_outlets") == 4


def test_dry_run_calls_no_api(conn, gdelt_rule, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("dry run must not call the API")
    monkeypatch.setattr(gdelt, "search", boom)

    report = scheduled.run(conn, dry_run=True)
    assert report.results == []
    assert any("dry run" in s for s in report.skipped)


# ------------------------------------------------------------------ the poller separation

def test_a_gdelt_rule_never_matches_feed_items(conn, campaign):
    """Regression: a gdelt_query rule has no feed of its own, so it used to fall through to its
    campaign's sibling RSS feeds and match every item in them — recording hits on unrelated
    stories, and, when marked promotes_to_live, setting a campaign's day zero off one.
    """
    from cib.models import WatchRule

    rule = WatchRule(id=1, name="GDELT", rule_type="gdelt_query", pattern="fishmeal")
    assert rule_repo.matches(
        rule, title="Completely unrelated story about shipping", url="https://other.example/x",
    ) is False


def test_the_feed_poller_skips_gdelt_rules_entirely(conn, monkeypatch):
    from cib.ingest.feeds import parse_feed

    forthcoming = campaign_repo.create(
        conn, name="Forthcoming", slug="soon", publisher_org="P",
        campaign_type="journalism", status="pre_publication", published_at=None,
    )
    # A promoting GDELT rule alongside an ordinary feed rule for the same campaign.
    rule_repo.create(conn, name="GDELT", rule_type="gdelt_query", pattern="fishmeal",
                     campaign_id=forthcoming, promotes_to_live=True)
    rule_repo.create(conn, name="Publisher feed", rule_type="rss",
                     pattern="https://publisher.example/feed", campaign_id=forthcoming)

    rss = """<?xml version="1.0"?><rss version="2.0"><channel><title>Publisher</title>
      <item><title>Unrelated story about shipping tariffs</title>
        <link>https://publisher.example/tariffs</link>
        <pubDate>Sun, 03 Mar 2019 06:00:00 +0000</pubDate></item>
    </channel></rss>"""
    monkeypatch.setattr(poller, "fetch", lambda url, timeout=30: parse_feed(rss))

    report = poller.poll_once(conn, notify=False)

    # Only the RSS rule was polled, and only it produced a hit.
    assert report.rules_polled == 1
    assert report.hits_new == 1
    hits = query(conn, """
        SELECT r.rule_type FROM watch_hits h JOIN watch_rules r ON r.id = h.rule_id
    """)
    assert [h["rule_type"] for h in hits] == ["rss"]


# ------------------------------------------------------------------ GDELT field mapping

def test_gdelt_country_names_are_mapped_to_iso_codes():
    """Every other source stores ISO alpha-2. An unmapped name would count as its own country."""
    assert gdelt.country_code("Denmark") == "DK"
    assert gdelt.country_code("United Kingdom") == "GB"
    assert gdelt.country_code("Mauritania") == "MR"
    assert gdelt.country_code(" Peru ") == "PE"
    # Unmapped is null, never a guess and never the raw name.
    assert gdelt.country_code("Wakanda") is None
    assert gdelt.country_code(None) is None
    # Tolerates an already-ISO value in case the API's shape changes.
    assert gdelt.country_code("DK") == "DK"


def test_gdelt_rows_map_onto_the_canonical_shape():
    rows = gdelt.to_rows(GDELT_ARTICLES)
    assert rows[0]["headline"] == "Fishmeal investigation lands"
    assert rows[0]["published_at"] == "2019-03-04T06:00:00"
    assert rows[0]["outlet_name"] == "outlet-a.example"
    assert rows[0]["country"] == "DK"
    assert rows[0]["language"] == "en"
    # No body: GDELT supplies none, and one must never be invented.
    assert rows[0]["body_text"] is None
    assert rows[1]["country"] == "NO"


def test_an_unmapped_country_becomes_a_reported_gap_not_a_wrong_country(conn, gdelt_rule,
                                                                       monkeypatch):
    _stub_search(monkeypatch, articles=[{
        "title": "Story from somewhere unmapped", "seendate": "20190304T060000Z",
        "url": "https://outlet-x.example/s", "domain": "outlet-x.example",
        "sourcecountry": "Somewhere Unmapped", "language": "English",
    }])
    scheduled.run(conn)

    from cib.metrics import campaign_metrics
    country = campaign_metrics(conn, "live").get("country_count")
    assert country.value == 0
    assert country.basis["articles_without_country"] == 1
    assert any("no country" in c for c in country.caveats)
