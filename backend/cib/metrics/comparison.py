"""The comparison engine — the single most important function in the tool.

A live campaign at day 5 must be compared against past campaigns *at their day 5*, not against
their lifetime totals. An unfair comparison is worse than no comparison, so the cutoff is applied
identically to every campaign and every metric, and when the caller does not name one it is
derived conservatively and stated loudly.

A campaign with no publication date has no footprint and cannot be given one. `compare` refuses
to produce footprint metrics for it and returns publisher precedent and logged pre-publication
signals instead, so "0 articles" can never be misread as "low risk".
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..db import query, query_one
from ..models import Campaign
from ..repo import campaigns as campaign_repo
from ..repo import imports as import_repo
from . import escalation, exposure, footprint, selectors, velocity
from .result import MetricSet


class PrePublicationError(Exception):
    """Raised when footprint metrics are requested for a campaign that has not published."""


def _data_quality(conn: sqlite3.Connection, campaign: Campaign,
                  at_day_index: int | None) -> dict[str, Any]:
    rows = selectors.articles(conn, campaign.id, at_day_index)
    outlet_ids = {int(r["outlet_id"]) for r in rows}
    missing_country = sum(1 for r in rows if not (r["country"] or r["outlet_country"]))
    with_reach = sum(1 for oid in outlet_ids if query_one(
        conn, "SELECT reach_value FROM outlets WHERE id = ?", (oid,))["reach_value"] is not None)
    with_body, total = selectors.articles_with_body(conn, campaign.id)
    return {
        "articles_in_window": len(rows),
        "articles_total_imported": total,
        "articles_undated": selectors.undated_count(conn, campaign.id),
        "articles_missing_country_pct": round(100.0 * missing_country / len(rows), 1) if rows else None,
        "articles_with_body_text_pct": round(100.0 * with_body / total, 1) if total else None,
        "outlets_missing_reach_pct": (
            round(100.0 * (len(outlet_ids) - with_reach) / len(outlet_ids), 1)
            if outlet_ids else None
        ),
        "last_import_at": import_repo.last_import_at(conn, campaign.id),
        "sources": [dict(r) for r in import_repo.sources_for_campaign(conn, campaign.id)],
    }


def max_observed_day_index(conn: sqlite3.Connection, campaign_id: int) -> int | None:
    row = query_one(
        conn,
        "SELECT MAX(day_index) AS m FROM articles WHERE campaign_id = ? AND day_index IS NOT NULL",
        (campaign_id,),
    )
    return int(row["m"]) if row and row["m"] is not None else None


def campaign_metrics(conn: sqlite3.Connection, campaign_ref: str | int,
                     at_day_index: int | None = None) -> MetricSet:
    """Every metric for one campaign, truncated at a day index.

    Raises PrePublicationError for a campaign with no publication date — see `prepublication_view`.
    """
    campaign = campaign_repo.resolve(conn, campaign_ref)
    if campaign.published_at is None:
        raise PrePublicationError(
            f"Campaign '{campaign.slug}' has no publication date. Footprint metrics are not "
            "defined for it, and a zero here would read as low risk. Use the pre-publication "
            "view instead."
        )

    result = MetricSet(
        campaign_id=campaign.id,
        campaign_slug=campaign.slug,
        campaign_name=campaign.name,
        at_day_index=at_day_index,
    )
    result.add_all(footprint.compute(conn, campaign.id, at_day_index))
    result.add_all(velocity.compute(conn, campaign.id, campaign.status, at_day_index))
    result.add_all(exposure.compute(conn, campaign.id, at_day_index))
    result.add_all(escalation.compute(
        conn, campaign.id, campaign.published_at, campaign.timezone, at_day_index
    ))
    result.data_quality = _data_quality(conn, campaign, at_day_index)

    if not campaign.date_is_exact:
        unit = campaign.published_at_precision
        slack = {"month": "up to 30 days", "year": "up to 12 months"}.get(unit, "an unknown span")
        result.caveats.append(
            f"This campaign's publication date is only known to the {unit} "
            f"({campaign.published_at[:7] if unit == 'month' else campaign.published_at[:4]}). "
            f"Day zero is assumed, so every day-aligned figure — days to peak, half-life, days to "
            f"90% of volume, and any comparison at a cutoff — could be out by {slack}. "
            "Pin the exact date with `cib campaign set-published` before quoting them."
        )

    observed = max_observed_day_index(conn, campaign.id)
    if result.data_quality["articles_total_imported"] == 0:
        result.caveats.append(
            "No articles have been imported for this campaign. Every footprint figure below is "
            "an empty dataset, not a measurement of low coverage."
        )
    if at_day_index is not None and observed is not None and observed < at_day_index:
        result.caveats.append(
            f"Coverage for this campaign only runs to day {observed}, short of the day "
            f"{at_day_index} cutoff. Its figures are complete; the window is not."
        )
    if result.data_quality["articles_undated"]:
        result.caveats.append(
            f"{result.data_quality['articles_undated']} article(s) could not be placed on the day "
            "axis and are excluded from every day-truncated figure."
        )
    return result


def prepublication_view(conn: sqlite3.Connection, campaign_ref: str | int) -> dict[str, Any]:
    """What we can honestly say about a campaign that has not published yet.

    Deliberately contains no footprint numbers. What it contains instead is (a) the measured
    footprint of the same publisher's previous investigations and (b) the internal signals already
    logged — the two things that actually inform a pre-publication assessment.
    """
    campaign = campaign_repo.resolve(conn, campaign_ref)
    if campaign.published_at is not None:
        raise ValueError(
            f"Campaign '{campaign.slug}' has published ({campaign.published_at}). Use "
            "campaign_metrics() for it."
        )

    precedent_rows = campaign_repo.precedents(conn, campaign.id)
    precedents = []
    for row in precedent_rows:
        pid = int(row["precedent_campaign_id"])
        precedent = campaign_repo.get(conn, pid)
        entry: dict[str, Any] = {
            "campaign_id": pid,
            "slug": row["slug"],
            "name": row["name"],
            "publisher_org": row["publisher_org"],
            "published_at": row["published_at"],
            "status": row["status"],
            "rationale": row["rationale"],
            "asserted_by": row["created_by"],
            "asserted_at": row["created_at"],
        }
        if precedent and precedent.published_at:
            ms = campaign_metrics(conn, pid, at_day_index=None)
            entry["footprint"] = {
                k: ms.metrics[k].to_dict()
                for k in ("unique_stories", "unique_outlets", "country_count",
                          "syndication_ratio", "escalation_count", "severity_weighted_total")
                if k in ms.metrics
            }
            entry["data_quality"] = ms.data_quality
            # Carried through deliberately: a precedent with nothing imported shows zeros, and
            # a zero here would read as "that investigation went nowhere" rather than "we have
            # not measured it yet".
            entry["caveats"] = ms.caveats
            entry["has_measured_footprint"] = ms.data_quality["articles_total_imported"] > 0
        precedents.append(entry)

    signals = [dict(r) for r in query(conn, """
        SELECT * FROM inbound_signals WHERE campaign_id = ? ORDER BY occurred_at
    """, (campaign.id,))]

    watch = [dict(r) for r in query(conn, """
        SELECT r.id, r.name, r.rule_type, r.pattern, r.source_url, r.enabled,
               r.promotes_to_live, r.last_polled_at, r.last_error,
               (SELECT COUNT(*) FROM watch_hits h WHERE h.rule_id = r.id) AS hits
          FROM watch_rules r WHERE r.campaign_id = ? ORDER BY r.id
    """, (campaign.id,))]

    hits = [dict(r) for r in query(conn, """
        SELECT h.*, r.name AS rule_name FROM watch_hits h
          JOIN watch_rules r ON r.id = h.rule_id
         WHERE r.campaign_id = ? ORDER BY h.detected_at DESC LIMIT 50
    """, (campaign.id,))]

    return {
        "mode": "pre_publication",
        "campaign": {
            "id": campaign.id, "slug": campaign.slug, "name": campaign.name,
            "publisher_org": campaign.publisher_org, "campaign_type": campaign.campaign_type,
            "status": campaign.status, "first_signal_at": campaign.first_signal_at,
            "themes": campaign.theme_list, "notes": campaign.notes,
        },
        "footprint_available": False,
        "footprint_refusal": (
            "This campaign has not published. It has no media footprint to measure, and a zero "
            "here would mean 'not yet happened', not 'low risk'. What follows is publisher "
            "precedent and logged internal signals."
        ),
        "publisher_precedent": precedents,
        "precedent_count": len(precedents),
        "inbound_signals": signals,
        "inbound_signal_count": len(signals),
        "watch_rules": watch,
        "watch_rules_enabled": sum(1 for w in watch if w["enabled"]),
        "watch_hits": hits,
    }


def resolve_cutoff(conn: sqlite3.Connection, campaigns: list[Campaign],
                   at_day_index: int | None) -> tuple[int | None, list[str]]:
    """Decide the day-index cutoff, and say why.

    When the caller names one, it is used as given. When they do not, the cutoff is the *shortest*
    observed window across the campaigns being compared: comparing a campaign's five days against
    another's five years is the failure mode this whole function exists to prevent.
    """
    if at_day_index is not None:
        return at_day_index, []
    observed = [max_observed_day_index(conn, c.id) for c in campaigns]
    usable = [o for o in observed if o is not None]
    if not usable:
        return None, [
            "No cutoff was given and no campaign has dated coverage, so no truncation was applied."
        ]
    cutoff = min(usable)
    notes = [
        f"No cutoff was given. Day {cutoff} was used automatically: it is the shortest observed "
        "window among the selected campaigns, so no campaign is compared against a longer run of "
        "another. Set an explicit cutoff to override."
    ]
    longer = [c.slug for c, o in zip(campaigns, observed, strict=False)
              if o is not None and o > cutoff]
    if longer:
        notes.append(
            "Truncated to that cutoff: " + ", ".join(longer) +
            ". Their lifetime totals are larger than shown here."
        )
    return cutoff, notes


def compare(conn: sqlite3.Connection, campaign_refs: list[str | int],
            at_day_index: int | None = None) -> dict[str, Any]:
    """Compare 2-4 campaigns at an identical day index.

    Returns a measured table, day-aligned cumulative series for charting, and the caveats that
    must be displayed with them. Pre-publication campaigns are separated out with their own view
    rather than being rendered as zeros.
    """
    if len(campaign_refs) < 2:
        raise ValueError("Comparison needs at least two campaigns.")
    if len(campaign_refs) > 4:
        raise ValueError(
            "Comparison is limited to four campaigns: beyond that the table stops being readable "
            "and people start quoting single columns out of context."
        )

    resolved = [campaign_repo.resolve(conn, ref) for ref in campaign_refs]
    published = [c for c in resolved if c.published_at is not None]
    unpublished = [c for c in resolved if c.published_at is None]

    cutoff, cutoff_notes = resolve_cutoff(conn, published, at_day_index)

    columns = []
    caveats = list(cutoff_notes)
    for campaign in published:
        ms = campaign_metrics(conn, campaign.id, cutoff)
        columns.append(ms.to_dict())
        caveats.extend(f"[{campaign.slug}] {c}" for c in ms.caveats)

    prepub = []
    for campaign in unpublished:
        view = prepublication_view(conn, campaign.id)
        prepub.append(view)
        caveats.append(
            f"[{campaign.slug}] Has not published. It is excluded from the footprint table by "
            "design and shown as publisher precedent and logged signals instead."
        )

    series = {
        c.slug: velocity.cumulative_series(conn, c.id, cutoff) for c in published
    }

    escalation_series = {
        c.slug: (campaign_metrics(conn, c.id, cutoff).get("severity_weighted_total").basis
                 .get("running_total", []))
        for c in published
    }

    return {
        "at_day_index": cutoff,
        "cutoff_was_explicit": at_day_index is not None,
        "campaigns": columns,
        "pre_publication": prepub,
        "caveats": caveats,
        "series": series,
        "escalation_series": escalation_series,
        "metric_order": METRIC_ORDER,
    }


# Display order for the comparison table. Footprint first, escalation last, because the escalation
# chain is what actually predicts whether a story becomes a problem.
METRIC_ORDER = [
    "unique_stories",
    "unique_outlets",
    "total_articles",
    "syndication_ratio",
    "country_count",
    "language_count",
    "tier_mix",
    "total_reach",
    "reach_coverage",
    "peak_day",
    "peak_volume",
    "days_to_peak",
    "half_life_days",
    "days_to_90pct",
    "long_tail",
    "own_company_mentions",
    "depth_score",
    "first_mention_day",
    "competitor_comparison",
    "escalation_count",
    "days_to_first_escalation",
    "severity_weighted_total",
    "escalation_chain",
]
