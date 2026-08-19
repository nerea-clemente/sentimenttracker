"""The scheduled poller.

For a campaign that has not yet published, this is the part of the tool that catches day zero.
It walks the enabled watch rules, fetches whatever feed each one points at, and records new hits.

Promotion: when a rule marked `promotes_to_live` hits, the campaign's published_at is set to the
matched item's publication time and daily snapshotting starts. That is a deliberate, narrow
power — reserved for rules precise enough to mean "it has published" — and it is recorded on the
hit row so the moment is auditable afterwards.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ..actor import actor, now_utc
from ..db import insert, query_one, transaction
from ..ingest.feeds import fetch
from ..ingest.http import FetchError
from ..models import WatchRule
from ..repo import articles as article_repo
from ..repo import campaigns as campaign_repo
from . import rules as rule_repo
from .notify import Alert, send

# Rules with their own feed. Content rules (keyword/phrase/byline/domain) are applied to the feeds
# of the rules that have one, plus any feed named on the rule itself.
FEED_RULE_TYPES = ("rss", "sitemap")


@dataclass
class PollReport:
    run_id: int | None = None
    rules_polled: int = 0
    hits_new: int = 0
    errors: int = 0
    promotions: list[dict] = field(default_factory=list)
    detail: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.rules_polled} rules polled", f"{self.hits_new} new hits"]
        if self.promotions:
            parts.append(f"{len(self.promotions)} campaign(s) promoted to live")
        if self.errors:
            parts.append(f"{self.errors} errors")
        return " | ".join(parts)


def _feed_urls(conn: sqlite3.Connection, rule: WatchRule) -> list[str]:
    if rule.rule_type in FEED_RULE_TYPES:
        return [rule.pattern]
    if rule.source_url:
        return [rule.source_url]
    # A content rule with no feed of its own is applied to every feed watched for its campaign.
    siblings = rule_repo.list_all(conn, enabled_only=True, campaign_id=rule.campaign_id)
    return [r.pattern for r in siblings if r.rule_type in FEED_RULE_TYPES]


def _promote(conn: sqlite3.Connection, rule: WatchRule, hit_id: int,
             occurred_at: str, matched_url: str) -> dict | None:
    if not rule.promotes_to_live or rule.campaign_id is None:
        return None
    campaign = campaign_repo.get(conn, rule.campaign_id)
    if campaign is None or campaign.published_at is not None:
        return None
    campaign_repo.set_published(conn, campaign.id, occurred_at, status="live")
    updated = article_repo.recompute_day_index(conn, campaign.id, occurred_at, campaign.timezone)
    conn.execute("UPDATE watch_hits SET promoted = 1 WHERE id = ?", (hit_id,))
    return {
        "campaign_id": campaign.id,
        "campaign_slug": campaign.slug,
        "published_at": occurred_at,
        "triggered_by_rule": rule.name,
        "matched_url": matched_url,
        "articles_reindexed": updated,
        "promoted_by": actor(),
        "promoted_at": now_utc(),
    }


def poll_once(conn: sqlite3.Connection, rule_ids: list[int] | None = None,
              notify: bool = True) -> PollReport:
    """Poll every enabled rule once. Safe to run on a schedule; hits are deduplicated by URL."""
    report = PollReport()
    run_id = insert(conn, "watch_runs", {
        "started_at": now_utc(), "rules_polled": 0, "hits_new": 0, "errors": 0,
        "created_by": actor(),
    })
    report.run_id = run_id

    active = rule_repo.list_all(conn, enabled_only=True)
    if rule_ids:
        active = [r for r in active if r.id in set(rule_ids)]

    # Fetch each distinct feed once, however many rules point at it.
    feed_cache: dict[str, list] = {}

    for rule in active:
        if rule.rule_type == "gdelt_query":
            # Driven by `cib ingest`, which queries the GDELT API. Polling it here would apply it
            # to whatever RSS feeds the campaign happens to watch.
            continue
        report.rules_polled += 1
        error: str | None = None
        for feed_url in _feed_urls(conn, rule):
            if feed_url not in feed_cache:
                try:
                    feed_cache[feed_url] = fetch(feed_url)
                except (FetchError, ValueError) as exc:
                    feed_cache[feed_url] = []
                    error = str(exc)
                    report.errors += 1
                    report.detail.append({"rule": rule.name, "feed": feed_url, "error": str(exc)})
                    continue
            for item in feed_cache[feed_url]:
                if not rule_repo.matches(rule, title=item.title, url=item.link,
                                         author=item.author, summary=item.summary):
                    continue
                hit_id = rule_repo.record_hit(
                    conn,
                    rule_id=rule.id,
                    matched_url=item.link,
                    matched_title=item.title,
                    occurred_at=item.published_at,
                    matched_excerpt=item.summary,
                    raw={"feed": feed_url, "source_title": item.source_title,
                         "author": item.author},
                )
                if hit_id is None:
                    continue   # already seen; never re-alert
                report.hits_new += 1

                promotion = _promote(conn, rule, hit_id, item.published_at or now_utc(), item.link)
                if promotion:
                    report.promotions.append(promotion)

                if notify:
                    campaign = (campaign_repo.get(conn, rule.campaign_id)
                                if rule.campaign_id else None)
                    outcome = send(Alert(
                        title=(
                            f"Watch hit: {rule.name}"
                            + (" — CAMPAIGN PROMOTED TO LIVE" if promotion else "")
                        ),
                        body=(item.summary or item.title or "")[:600],
                        url=item.link,
                        campaign_slug=campaign.slug if campaign else None,
                        rule_name=rule.name,
                    ))
                    conn.execute("UPDATE watch_hits SET notified_at = ? WHERE id = ?",
                                 (now_utc(), hit_id))
                    report.detail.append({"rule": rule.name, "hit": item.link,
                                          "notify": outcome})
        rule_repo.mark_polled(conn, rule.id, error)

    conn.execute("""
        UPDATE watch_runs SET finished_at = ?, rules_polled = ?, hits_new = ?, errors = ?
         WHERE id = ?
    """, (now_utc(), report.rules_polled, report.hits_new, report.errors, run_id))
    return report


def daily_snapshot(conn: sqlite3.Connection) -> dict[str, int]:
    """Write metrics_daily rows for every live and decaying campaign."""
    from ..metrics import snapshot

    with transaction(conn):
        return snapshot.rebuild_live(conn)


def last_run(conn: sqlite3.Connection):
    return query_one(conn, "SELECT * FROM watch_runs ORDER BY id DESC LIMIT 1")


def run_scheduler(db_path: str | None = None, poll_minutes: int = 30,
                  snapshot_hour: int = 3) -> None:
    """Long-running scheduler: poll on an interval, snapshot once a day.

    Needs the `watch` extra (APScheduler). For a cron-based deployment, call
    `cib watch poll` and `cib snapshot` from cron instead — the tool does not require a daemon.
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError(
            "APScheduler is not installed. Either `pip install -e '.[watch]'` or schedule "
            "`cib watch poll` and `cib snapshot` with cron."
        ) from exc

    from ..db import connect

    def _poll() -> None:
        conn = connect(db_path)
        try:
            with transaction(conn):
                print(f"[{now_utc()}] {poll_once(conn).summary()}")
        finally:
            conn.close()

    def _snapshot() -> None:
        conn = connect(db_path)
        try:
            written = daily_snapshot(conn)
            print(f"[{now_utc()}] snapshot: {written}")
        finally:
            conn.close()

    scheduler = BlockingScheduler()
    scheduler.add_job(_poll, IntervalTrigger(minutes=poll_minutes), id="poll")
    scheduler.add_job(_snapshot, CronTrigger(hour=snapshot_hour, minute=0), id="snapshot")
    print(f"Polling every {poll_minutes}m; snapshotting daily at {snapshot_hour:02d}:00. Ctrl-C to stop.")
    scheduler.start()
