"""The trigger system, including the promotion that sets a campaign's day zero."""

from __future__ import annotations

from cib.db import query
from cib.ingest.feeds import parse_feed
from cib.models import WatchRule
from cib.repo import campaigns as campaign_repo
from cib.watch import poller
from cib.watch import rules as rule_repo

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Example Publisher</title>
  <item>
    <title>Investigation: fishmeal and the aquaculture supply chain</title>
    <link>https://publisher.example/investigation</link>
    <pubDate>Mon, 04 Mar 2019 06:00:00 +0000</pubDate>
    <description>An eighteen-month investigation into fishmeal sourcing.</description>
    <dc:creator xmlns:dc="http://purl.org/dc/elements/1.1/">Karin Holm</dc:creator>
  </item>
  <item>
    <title>Unrelated story about shipping tariffs</title>
    <link>https://publisher.example/tariffs</link>
    <pubDate>Sun, 03 Mar 2019 06:00:00 +0000</pubDate>
  </item>
</channel></rss>
"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Newsletter</title>
  <entry>
    <title>Coming soon: our fishmeal investigation</title>
    <link rel="alternate" href="https://newsletter.example/teaser"/>
    <published>2019-03-01T09:00:00Z</published>
    <summary>A preview of next week's reporting.</summary>
    <author><name>Karin Holm</name></author>
  </entry>
</feed>
"""


def test_rss_and_atom_parse():
    items = parse_feed(RSS)
    assert [i.title for i in items] == [
        "Investigation: fishmeal and the aquaculture supply chain",
        "Unrelated story about shipping tariffs",
    ]
    assert items[0].published_at.startswith("2019-03-04")
    assert items[0].author == "Karin Holm"

    atom = parse_feed(ATOM)
    assert len(atom) == 1
    assert atom[0].link == "https://newsletter.example/teaser"
    assert atom[0].published_at.startswith("2019-03-01")


def _rule(**kwargs) -> WatchRule:
    base = {"id": 1, "name": "r", "rule_type": "keyword", "pattern": "fishmeal"}
    return WatchRule(**{**base, **kwargs})


def test_rule_matching_by_type():
    item = parse_feed(RSS)[0]
    kwargs = {"title": item.title, "url": item.link, "author": item.author,
              "summary": item.summary}

    assert rule_repo.matches(_rule(pattern="fishmeal aquaculture"), **kwargs)
    assert not rule_repo.matches(_rule(pattern="fishmeal tariffs"), **kwargs)
    assert rule_repo.matches(_rule(rule_type="phrase", pattern="aquaculture supply chain"), **kwargs)
    assert not rule_repo.matches(_rule(rule_type="phrase", pattern="supply chain aquaculture"), **kwargs)
    assert rule_repo.matches(_rule(rule_type="byline", pattern="Karin Holm"), **kwargs)
    assert not rule_repo.matches(_rule(rule_type="byline", pattern="Someone Else"), **kwargs)
    assert rule_repo.matches(_rule(rule_type="domain", pattern="publisher.example"), **kwargs)
    assert not rule_repo.matches(_rule(rule_type="domain", pattern="other.example"), **kwargs)
    # A feed-shaped rule is its own filter: the feed decides what it returns.
    assert rule_repo.matches(_rule(rule_type="rss", pattern="https://publisher.example/feed"),
                             **kwargs)


def test_a_repolled_item_does_not_realert(conn):
    rule_id = rule_repo.create(conn, name="Publisher feed", rule_type="rss",
                               pattern="https://publisher.example/feed")
    first = rule_repo.record_hit(conn, rule_id=rule_id, matched_url="https://publisher.example/a",
                                 matched_title="A", occurred_at="2019-03-04T06:00:00")
    second = rule_repo.record_hit(conn, rule_id=rule_id, matched_url="https://publisher.example/a",
                                  matched_title="A", occurred_at="2019-03-04T06:00:00")
    assert first is not None
    assert second is None
    assert int(query(conn, "SELECT COUNT(*) AS n FROM watch_hits")[0]["n"]) == 1


def test_a_promoting_rule_sets_day_zero_and_reindexes(conn, monkeypatch, sample_csv):
    """The whole point of the watch system: catching the moment a campaign publishes."""
    from cib.ingest import csv_generic

    campaign_id = campaign_repo.create(
        conn, name="Forthcoming", publisher_org="Example Publisher",
        campaign_type="journalism", status="pre_publication", published_at=None,
    )
    # Coverage imported before publication has no day axis to sit on yet.
    csv_generic.import_file(conn, campaign_ref=campaign_id, path=sample_csv)
    assert conn.execute(
        "SELECT COUNT(*) FROM articles WHERE campaign_id = ? AND day_index IS NULL", (campaign_id,)
    ).fetchone()[0] == 12

    rule_repo.create(conn, name="Publisher feed", rule_type="rss",
                     pattern="https://publisher.example/feed", campaign_id=campaign_id,
                     promotes_to_live=True)

    monkeypatch.setattr(poller, "fetch", lambda url, timeout=30: parse_feed(RSS))
    report = poller.poll_once(conn, notify=False)

    assert report.hits_new == 2
    assert len(report.promotions) == 1
    promotion = report.promotions[0]
    assert promotion["campaign_slug"] == campaign_repo.get(conn, campaign_id).slug
    assert promotion["articles_reindexed"] == 12

    campaign = campaign_repo.get(conn, campaign_id)
    assert campaign.status == "live"
    assert campaign.published_at is not None
    assert conn.execute(
        "SELECT COUNT(*) FROM articles WHERE campaign_id = ? AND day_index IS NULL", (campaign_id,)
    ).fetchone()[0] == 0


def test_promotion_happens_only_once(conn, monkeypatch):
    campaign_id = campaign_repo.create(
        conn, name="Forthcoming", publisher_org="P", campaign_type="journalism",
        status="pre_publication", published_at=None,
    )
    rule_repo.create(conn, name="Feed", rule_type="rss", pattern="https://publisher.example/feed",
                     campaign_id=campaign_id, promotes_to_live=True)
    monkeypatch.setattr(poller, "fetch", lambda url, timeout=30: parse_feed(RSS))

    first = poller.poll_once(conn, notify=False)
    published_at = campaign_repo.get(conn, campaign_id).published_at
    second = poller.poll_once(conn, notify=False)

    assert len(first.promotions) == 1
    assert second.promotions == []
    assert second.hits_new == 0
    assert campaign_repo.get(conn, campaign_id).published_at == published_at


def test_a_poll_run_is_recorded_even_when_it_finds_nothing(conn, monkeypatch):
    rule_repo.create(conn, name="Feed", rule_type="rss", pattern="https://publisher.example/feed")
    monkeypatch.setattr(poller, "fetch", lambda url, timeout=30: [])
    poller.poll_once(conn, notify=False)

    run = poller.last_run(conn)
    assert run is not None and run["finished_at"] and run["rules_polled"] == 1
    assert run["hits_new"] == 0


def test_a_feed_error_is_recorded_on_the_rule_not_swallowed(conn, monkeypatch):
    from cib.ingest.http import FetchError

    rule_id = rule_repo.create(conn, name="Feed", rule_type="rss",
                               pattern="https://publisher.example/feed")

    def boom(url, timeout=30):
        raise FetchError("connection refused")

    monkeypatch.setattr(poller, "fetch", boom)
    report = poller.poll_once(conn, notify=False)

    assert report.errors == 1
    assert "connection refused" in rule_repo.get(conn, rule_id).last_error


def test_seed_watch_rules_are_disabled(conn):
    """A rule pointing at a placeholder domain would poll nothing while looking healthy."""
    from cib import seed

    seed.run(conn)
    rules = rule_repo.list_all(conn)
    assert rules
    assert all(r.enabled == 0 for r in rules)
    assert rule_repo.list_all(conn, enabled_only=True) == []


def test_notification_channels_report_per_channel_outcome(monkeypatch):
    from cib.watch.notify import Alert, send

    sent = []
    monkeypatch.setitem(__import__("cib.watch.notify", fromlist=["CHANNELS"]).CHANNELS,
                        "stdout", lambda alert: sent.append(alert))
    outcome = send(Alert(title="t", body="b"), channels="stdout,does_not_exist")
    assert outcome["stdout"] == "sent"
    assert "unknown channel" in outcome["does_not_exist"]
    assert len(sent) == 1


def test_a_feed_with_a_doctype_is_refused():
    """Feeds are untrusted input; a DTD is how an entity-expansion bomb arrives."""
    import pytest

    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]>'
        '<rss version="2.0"><channel><title>&lol;</title></channel></rss>'
    )
    with pytest.raises(ValueError, match="DOCTYPE"):
        parse_feed(bomb)
