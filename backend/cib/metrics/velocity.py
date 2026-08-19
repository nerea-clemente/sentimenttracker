"""Velocity and persistence: how fast a campaign escalated and how long it lasted."""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from . import selectors
from .result import MetricValue, metric, unavailable

HALF_LIFE_FRACTION = 0.10
TRAILING_WINDOW = 3
LONG_TAIL_DAY = 30


def daily_counts(rows) -> dict[int, list[int]]:
    """day_index -> article ids on that day. Days with no coverage are absent, not zero."""
    per_day: dict[int, list[int]] = defaultdict(list)
    for r in rows:
        if r["day_index"] is not None:
            per_day[int(r["day_index"])].append(int(r["id"]))
    return dict(per_day)


def _dense_series(per_day: dict[int, list[int]]) -> list[tuple[int, int]]:
    """Fill the gaps: every day between first and last coverage, zero-filled.

    Half-life needs real zeros, otherwise a two-week silence looks like no data rather than decay.
    """
    if not per_day:
        return []
    lo, hi = min(per_day), max(per_day)
    return [(d, len(per_day.get(d, ()))) for d in range(lo, hi + 1)]


def compute(conn: sqlite3.Connection, campaign_id: int, campaign_status: str,
            at_day_index: int | None = None) -> list[MetricValue]:
    rows = selectors.articles(conn, campaign_id, at_day_index)
    per_day = daily_counts(rows)
    out: list[MetricValue] = []

    if not per_day:
        reason = "No dated articles imported for this campaign."
        return [
            unavailable("peak_day", reason),
            unavailable("peak_volume", reason),
            unavailable("days_to_peak", reason),
            unavailable("half_life_days", reason),
            unavailable("days_to_90pct", reason),
            metric("long_tail", False, row_table="articles", row_ids=[],
                   basis={"articles_after_day_30": 0}),
        ]

    # Peak: the earliest day holding the maximum count. Ties resolve to the earlier day, so a
    # campaign is never credited with peaking later than it did.
    peak_volume = max(len(ids) for ids in per_day.values())
    peak_day = min(d for d, ids in per_day.items() if len(ids) == peak_volume)

    out.append(metric("peak_day", peak_day, row_table="articles",
                      row_ids=per_day[peak_day], basis={"peak_volume": peak_volume}))
    out.append(metric("peak_volume", peak_volume, row_table="articles",
                      row_ids=per_day[peak_day], basis={"peak_day": peak_day}))
    out.append(metric("days_to_peak", peak_day, row_table="articles",
                      row_ids=per_day[peak_day],
                      basis={"note": "Negative means coverage peaked before the publication date."}))

    # Half-life: days from peak until the 3-day trailing mean drops below a tenth of peak.
    series = _dense_series(per_day)
    threshold = peak_volume * HALF_LIFE_FRACTION
    half_life = None
    crossing_day = None
    for i, (day, _count) in enumerate(series):
        if day <= peak_day:
            continue
        window = [c for _d, c in series[max(0, i - TRAILING_WINDOW + 1):i + 1]]
        if sum(window) / len(window) < threshold:
            crossing_day = day
            half_life = day - peak_day
            break
    if half_life is None:
        out.append(unavailable(
            "half_life_days",
            "Daily volume has not yet fallen below 10% of peak within the imported window. "
            "This is an open campaign, not a zero.",
            basis={"peak_day": peak_day, "peak_volume": peak_volume,
                   "threshold": round(threshold, 2),
                   "last_day_observed": series[-1][0]},
        ))
    else:
        # The evidence for a half-life is the decay itself: the peak day's articles and everything
        # published between the peak and the crossing day. The crossing day is often empty — that
        # is what crossing means — so pointing only at it would leave the figure untraceable.
        decay_ids = [aid for day in range(peak_day, crossing_day + 1)
                     for aid in per_day.get(day, ())]
        out.append(metric(
            "half_life_days", half_life, row_table="articles",
            row_ids=decay_ids,
            basis={"peak_day": peak_day, "peak_volume": peak_volume,
                   "threshold": round(threshold, 2), "crossed_on_day": crossing_day,
                   "articles_on_crossing_day": len(per_day.get(crossing_day, ())),
                   "trailing_window_days": TRAILING_WINDOW},
        ))

    # Days to 90% of cumulative volume. Meaningless while a campaign is still running, because the
    # denominator is not final — so it refuses rather than returning a falsely reassuring number.
    if campaign_status in ("live", "pre_publication"):
        out.append(unavailable(
            "days_to_90pct",
            f"Campaign status is '{campaign_status}'. Its total volume is not final, so a "
            "percentage of that total would be misleading.",
        ))
    else:
        total = sum(len(ids) for ids in per_day.values())
        target = 0.9 * total
        running = 0
        day_at_90 = None
        contributing: list[int] = []
        for day in sorted(per_day):
            running += len(per_day[day])
            contributing.extend(per_day[day])
            if running >= target:
                day_at_90 = day
                break
        out.append(metric(
            "days_to_90pct", day_at_90, row_table="articles", row_ids=contributing,
            basis={"total_articles": total, "target_articles": round(target, 1),
                   "articles_by_that_day": running},
        ))

    tail_ids = [aid for day, ids in per_day.items() if day > LONG_TAIL_DAY for aid in ids]
    out.append(metric(
        "long_tail", bool(tail_ids), row_table="articles", row_ids=sorted(tail_ids),
        basis={"articles_after_day_30": len(tail_ids), "threshold_day": LONG_TAIL_DAY},
    ))

    return out


def cumulative_series(conn: sqlite3.Connection, campaign_id: int,
                      at_day_index: int | None = None) -> list[dict]:
    """Day-aligned cumulative series for the comparison charts.

    Cumulative articles, cumulative unique outlets and cumulative countries, zero-filled across
    every day in range so two campaigns plot against the same x-axis.
    """
    rows = selectors.articles(conn, campaign_id, at_day_index)
    if not rows:
        return []
    from .footprint import article_country

    by_day: dict[int, list] = defaultdict(list)
    for r in rows:
        if r["day_index"] is not None:
            by_day[int(r["day_index"])].append(r)
    if not by_day:
        return []

    lo, hi = min(by_day), max(by_day)
    seen_outlets: set[int] = set()
    seen_countries: set[str] = set()
    cumulative = 0
    series = []
    for day in range(lo, hi + 1):
        todays = by_day.get(day, [])
        cumulative += len(todays)
        for r in todays:
            seen_outlets.add(int(r["outlet_id"]))
            c = article_country(r)
            if c:
                seen_countries.add(c)
        series.append({
            "day_index": day,
            "articles": len(todays),
            "cumulative_articles": cumulative,
            "unique_outlets_today": len({int(r["outlet_id"]) for r in todays}),
            "cumulative_unique_outlets": len(seen_outlets),
            "cumulative_countries": len(seen_countries),
        })
    return series
