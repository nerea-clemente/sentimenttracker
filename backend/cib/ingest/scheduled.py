"""Automated ingestion, driven by configured watch rules.

Archive coverage (Infomedia, Factiva, Nexis) is licensed and cannot be fetched, so it arrives by
hand. Everything else can be pulled on a schedule, and this is the driver the scheduled workflow
calls: for every enabled rule that names an automated source, fetch the window since that rule was
last run and import it into the rule's campaign.

Two rule types are handled:

  * `gdelt_query` — GDELT DOC 2.0. No API key. Gives cross-country volume and outlet identity
    within minutes of a story breaking, but no article body, so those rows cannot be scanned for
    entity mentions and the exposure metrics report that gap rather than assuming zero.
  * `rss` / `sitemap` — the publisher and trade-press feeds. Only imported when the rule opts in
    with `ingest` in its notes, because most feed rules exist to *detect* publication rather than
    to be a coverage source, and importing a whole publisher feed would bury the campaign in
    unrelated articles.

The window is taken from the rule's `last_polled_at`, minus an overlap. Re-importing an overlapping
window is free: articles are keyed by (campaign, url), so anything already present is counted as a
duplicate rather than inserted twice.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..actor import now_utc
from ..models import WatchRule
from ..repo import campaigns as campaign_repo
from ..timeutil import parse_timestamp
from ..watch import rules as rule_repo
from .http import FetchError

# GDELT's article list caps at 250 records per call and cannot be paged; the only way to get more
# is a narrower time window. Hitting the cap means coverage was silently dropped, so it is
# reported rather than swallowed.
GDELT_MAX_RECORDS = 250

# Re-fetch a day either side of the watermark. GDELT's crawl time lags publication, so an article
# published just before the last run can appear just after it.
OVERLAP_HOURS = 24

# How far back to look the first time a rule runs.
DEFAULT_LOOKBACK_DAYS = 7


@dataclass
class SourceResult:
    rule_id: int
    rule_name: str
    campaign_slug: str
    source: str
    window_start: str
    window_end: str
    rows_seen: int = 0
    inserted: int = 0
    duplicates: int = 0
    rejected: int = 0
    error: str | None = None
    hit_record_cap: bool = False


@dataclass
class IngestReport:
    started_at: str = field(default_factory=now_utc)
    results: list[SourceResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def inserted(self) -> int:
        return sum(r.inserted for r in self.results)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.error)

    def summary(self) -> str:
        parts = [
            f"{len(self.results)} source(s) run",
            f"{self.inserted} new article(s)",
            f"{sum(r.duplicates for r in self.results)} already present",
        ]
        if self.errors:
            parts.append(f"{self.errors} error(s)")
        capped = [r for r in self.results if r.hit_record_cap]
        if capped:
            parts.append(f"{len(capped)} source(s) hit the record cap")
        return " | ".join(parts)


def _gdelt_stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S")


def _window(rule: WatchRule, lookback_days: int) -> tuple[datetime, datetime]:
    """The time range to fetch: since the rule last ran, with an overlap, else the lookback."""
    end = datetime.utcnow()
    watermark = parse_timestamp(rule.last_polled_at) if rule.last_polled_at else None
    if watermark is None:
        return end - timedelta(days=lookback_days), end
    if watermark.tzinfo is not None:
        watermark = watermark.replace(tzinfo=None)
    start = watermark - timedelta(hours=OVERLAP_HOURS)
    # Never fetch a window wider than the lookback: a rule dormant for a year would otherwise ask
    # GDELT for a year and get an arbitrary 250 articles out of it.
    floor = end - timedelta(days=lookback_days)
    return (max(start, floor), end)


def _ingest_gdelt(conn: sqlite3.Connection, rule: WatchRule, campaign,
                  lookback_days: int) -> SourceResult:
    from . import gdelt

    start, end = _window(rule, lookback_days)
    result = SourceResult(
        rule_id=rule.id, rule_name=rule.name, campaign_slug=campaign.slug, source="gdelt",
        window_start=_gdelt_stamp(start), window_end=_gdelt_stamp(end),
    )
    try:
        report = gdelt.import_query(
            conn, campaign_ref=campaign.id, query=rule.pattern,
            start=result.window_start, end=result.window_end,
            max_records=GDELT_MAX_RECORDS,
        )
    except FetchError as exc:
        result.error = str(exc)
        return result

    result.rows_seen = report.row_count
    result.inserted = report.inserted
    result.duplicates = report.skipped_duplicate
    result.rejected = report.rejected
    result.hit_record_cap = report.row_count >= GDELT_MAX_RECORDS
    return result


def _ingest_feed(conn: sqlite3.Connection, rule: WatchRule, campaign) -> SourceResult:
    from . import feeds

    result = SourceResult(
        rule_id=rule.id, rule_name=rule.name, campaign_slug=campaign.slug, source="rss",
        window_start="", window_end=now_utc(),
    )
    try:
        report = feeds.import_feed(conn, campaign_ref=campaign.id, url=rule.pattern)
    except (FetchError, ValueError) as exc:
        result.error = str(exc)
        return result

    result.rows_seen = report.row_count
    result.inserted = report.inserted
    result.duplicates = report.skipped_duplicate
    result.rejected = report.rejected
    return result


def _feed_rule_opts_in(rule: WatchRule) -> bool:
    """A feed rule is a coverage source only when its notes say so.

    Most feed rules exist to catch the moment a campaign publishes. Importing every item a
    publisher's feed carries would fill the campaign with unrelated articles and corrupt every
    footprint figure, so opting in is explicit.
    """
    return "ingest" in (rule.notes or "").lower()


def run(conn: sqlite3.Connection, *, lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        rule_ids: list[int] | None = None, dry_run: bool = False) -> IngestReport:
    """Import from every enabled rule that names an automated source."""
    report = IngestReport()
    rules = rule_repo.list_all(conn, enabled_only=True)
    if rule_ids:
        rules = [r for r in rules if r.id in set(rule_ids)]

    for rule in rules:
        automated = rule.rule_type == "gdelt_query" or (
            rule.rule_type in ("rss", "sitemap") and _feed_rule_opts_in(rule)
        )
        if not automated:
            continue

        if rule.campaign_id is None:
            report.skipped.append(
                f"rule #{rule.id} '{rule.name}' names an automated source but no campaign, so "
                "there is nowhere to import into."
            )
            continue
        campaign = campaign_repo.get(conn, rule.campaign_id)
        if campaign is None:  # pragma: no cover - defensive
            report.skipped.append(f"rule #{rule.id} points at a campaign that no longer exists.")
            continue

        if dry_run:
            start, end = _window(rule, lookback_days)
            report.skipped.append(
                f"dry run: would fetch {rule.rule_type} '{rule.pattern}' for {campaign.slug} "
                f"({_gdelt_stamp(start)}..{_gdelt_stamp(end)})"
            )
            continue

        if rule.rule_type == "gdelt_query":
            result = _ingest_gdelt(conn, rule, campaign, lookback_days)
        else:
            result = _ingest_feed(conn, rule, campaign)

        report.results.append(result)
        # The watermark only advances on success, so a failed run re-fetches its window next time
        # rather than leaving a hole in the coverage nothing would ever notice.
        if result.error:
            rule_repo.mark_failed(conn, rule.id, result.error)
        else:
            rule_repo.mark_polled(conn, rule.id, None)

    return report


def cluster_and_reindex(conn: sqlite3.Connection, report: IngestReport) -> dict[str, int]:
    """Re-run syndication clustering for every campaign this ingest touched.

    New articles arriving into an existing campaign can be republications of stories already
    imported, so unique-story counts are wrong until clustering runs again.
    """
    from .. import dedup

    touched = {r.campaign_slug for r in report.results if r.inserted}
    out: dict[str, int] = {}
    for slug in sorted(touched):
        campaign = campaign_repo.get_by_slug(conn, slug)
        if campaign is None:  # pragma: no cover - defensive
            continue
        cluster_report = dedup.cluster_campaign(conn, campaign.id)
        out[slug] = cluster_report.clusters_created
    return out
