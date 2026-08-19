"""Footprint metrics: how much coverage, across how many outlets, countries and languages.

The central distinction here is unique stories versus unique outlets. Reporting only one of them
is how a wire pickup gets mistaken for a wave of independent interest.
"""

from __future__ import annotations

import sqlite3
from collections import Counter

from . import selectors
from .result import MetricValue, metric


def _story_groups(rows) -> tuple[list[int], dict[int, list[int]]]:
    """Collapse rows into stories.

    A story is one syndication cluster, or one unclustered article. Returns the representative
    article id per story and, for each, the article ids that make it up — so a "unique stories"
    figure drills down to the actual cluster members.
    """
    clusters: dict[int, list[int]] = {}
    singletons: list[int] = []
    for r in rows:
        aid = int(r["id"])
        if r["cluster_id"] is None:
            singletons.append(aid)
        else:
            clusters.setdefault(int(r["cluster_id"]), []).append(aid)

    representatives: list[int] = []
    members: dict[int, list[int]] = {}
    for _cid, ids in clusters.items():
        rep = min(ids)
        representatives.append(rep)
        members[rep] = sorted(ids)
    for aid in singletons:
        representatives.append(aid)
        members[aid] = [aid]
    return sorted(representatives), members


def article_country(row) -> str | None:
    """The article's country, falling back to the outlet's. None when genuinely unknown."""
    return (row["country"] or row["outlet_country"]) or None


def article_language(row) -> str | None:
    return (row["language"] or row["outlet_language"]) or None


def compute(conn: sqlite3.Connection, campaign_id: int,
            at_day_index: int | None = None) -> list[MetricValue]:
    rows = selectors.articles(conn, campaign_id, at_day_index)
    all_ids = [int(r["id"]) for r in rows]

    reps, members = _story_groups(rows)
    outlet_ids = sorted({int(r["outlet_id"]) for r in rows})

    out: list[MetricValue] = []

    out.append(metric(
        "total_articles", len(rows), row_table="articles", row_ids=all_ids,
    ))

    out.append(metric(
        "unique_stories", len(reps), row_table="articles", row_ids=reps,
        basis={
            "clustered_articles": sum(1 for r in rows if r["cluster_id"] is not None),
            "unclustered_articles": sum(1 for r in rows if r["cluster_id"] is None),
            "story_members": {str(k): v for k, v in members.items()},
        },
    ))

    out.append(metric(
        "unique_outlets", len(outlet_ids), row_table="outlets", row_ids=outlet_ids,
        basis={"total_articles": len(rows)},
    ))

    # Syndication ratio: outlets per story. Undefined with no stories — reported as null, not 0.
    if reps:
        ratio = round(len(outlet_ids) / len(reps), 3)
        out.append(metric(
            "syndication_ratio", ratio, row_table="articles", row_ids=all_ids,
            basis={"unique_outlets": len(outlet_ids), "unique_stories": len(reps)},
        ))
    else:
        out.append(metric(
            "syndication_ratio", None, row_table="articles", row_ids=[],
            basis={"unique_outlets": 0, "unique_stories": 0},
            caveats=["No stories imported, so the ratio is undefined rather than zero."],
        ))

    # Tier mix, as counts and share.
    tier_counts = Counter(r["outlet_tier"] for r in rows)
    tier_rows: dict[str, list[int]] = {}
    for r in rows:
        tier_rows.setdefault(r["outlet_tier"], []).append(int(r["id"]))
    total = len(rows) or 1
    out.append(metric(
        "tier_mix",
        {t: {"articles": n, "share": round(n / total, 4)} for t, n in tier_counts.most_common()},
        row_table="articles", row_ids=all_ids,
        basis={"row_ids_by_tier": tier_rows, "total_articles": len(rows)},
    ))

    # Countries and languages, with the unknown share made explicit.
    countries = Counter(c for c in (article_country(r) for r in rows) if c)
    unknown_country = sum(1 for r in rows if not article_country(r))
    country_caveats = []
    if unknown_country:
        country_caveats.append(
            f"{unknown_country} of {len(rows)} articles have no country on the article or the "
            "outlet and are excluded from the country count."
        )
    out.append(metric(
        "country_count", len(countries), row_table="articles", row_ids=all_ids,
        basis={
            "distribution": dict(countries.most_common()),
            "articles_without_country": unknown_country,
            "articles_total": len(rows),
        },
        caveats=country_caveats,
    ))

    languages = Counter(lang for lang in (article_language(r) for r in rows) if lang)
    unknown_language = sum(1 for r in rows if not article_language(r))
    out.append(metric(
        "language_count", len(languages), row_table="articles", row_ids=all_ids,
        basis={
            "distribution": dict(languages.most_common()),
            "articles_without_language": unknown_language,
            "articles_total": len(rows),
        },
    ))

    # Reach, only where sourced. The coverage percentage always travels with the sum.
    reach_by_outlet: dict[int, int] = {}
    estimated_outlets: list[int] = []
    for r in rows:
        oid = int(r["outlet_id"])
        if r["reach_value"] is not None and oid not in reach_by_outlet:
            reach_by_outlet[oid] = int(r["reach_value"])
            if r["reach_is_estimated"]:
                estimated_outlets.append(oid)

    known = len(reach_by_outlet)
    coverage = round(100.0 * known / len(outlet_ids), 1) if outlet_ids else None
    reach_caveats = []
    if outlet_ids and known < len(outlet_ids):
        reach_caveats.append(
            f"Only {known} of {len(outlet_ids)} outlets have a sourced reach figure "
            f"({coverage}%). The sum is a floor, not a total."
        )
    if estimated_outlets:
        reach_caveats.append(
            f"{len(estimated_outlets)} of those outlets carry an estimated reach figure; the "
            "estimation method is recorded on each outlet."
        )
    out.append(metric(
        "total_reach", sum(reach_by_outlet.values()) if reach_by_outlet else None,
        row_table="outlets", row_ids=sorted(reach_by_outlet),
        basis={
            "outlets_with_reach": known,
            "outlets_total": len(outlet_ids),
            "coverage_pct": coverage,
            "estimated_outlet_ids": sorted(estimated_outlets),
        },
        caveats=reach_caveats,
    ))
    out.append(metric(
        "reach_coverage", coverage, row_table="outlets", row_ids=outlet_ids,
        basis={"outlets_with_reach": known, "outlets_total": len(outlet_ids)},
    ))

    return out
