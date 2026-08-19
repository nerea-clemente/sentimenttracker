"""Plain-language definitions of every metric.

These will be argued about more than the code will, so they live in one place, are attached to
every value the engine returns, and are rendered verbatim in the UI and in the briefing export.
A metric with no definition here fails the test suite.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Definition:
    key: str
    label: str
    unit: str
    plain: str
    counts: str          # exactly which rows are counted
    caveat: str = ""     # what this figure cannot tell you


_D = [
    # --- Footprint -------------------------------------------------------------------------
    Definition(
        "unique_stories", "Unique stories", "stories",
        "How many distinct pieces of journalism exist, after collapsing syndicated republications "
        "of the same text into one.",
        "One per syndication cluster, plus every article that is in no cluster.",
        "A cluster is a judgement made by the deduplication pass. Check the cluster list before "
        "quoting this figure in a contested setting.",
    ),
    Definition(
        "unique_outlets", "Unique outlets", "outlets",
        "How many separate publications carried the story, counting a syndicated republication as "
        "a real appearance because a reader of that outlet did see it.",
        "COUNT(DISTINCT outlet_id) across all articles, before clustering.",
    ),
    Definition(
        "total_articles", "Total articles", "articles",
        "Every imported item, including syndicated duplicates.",
        "Every article row for the campaign within the day-index cutoff.",
    ),
    Definition(
        "syndication_ratio", "Syndication ratio", "outlets per story",
        "How far the average story travelled. 1.0 means every outlet wrote its own piece; 8.0 "
        "means one story reached eight outlets.",
        "Unique outlets divided by unique stories.",
        "A high ratio is wire pickup, not independent interest. It inflates outlet counts without "
        "adding newsroom attention.",
    ),
    Definition(
        "tier_mix", "Outlet tier mix", "articles",
        "Where the coverage sits: national press, trade press, wires, NGO channels and so on.",
        "Articles grouped by their outlet's tier, as counts and as a share of all articles.",
    ),
    Definition(
        "country_count", "Countries", "countries",
        "How many countries carried coverage.",
        "COUNT(DISTINCT country) over articles, falling back to the outlet's country when the "
        "article row has none.",
        "Articles with no country on either the article or the outlet are excluded and reported "
        "separately as a coverage gap.",
    ),
    Definition(
        "language_count", "Languages", "languages",
        "How many languages the coverage appeared in.",
        "COUNT(DISTINCT language) over articles, falling back to the outlet's language.",
    ),
    Definition(
        "total_reach", "Total reach where sourced", "people",
        "The summed audience of the outlets that carried coverage, counting only outlets whose "
        "reach figure comes from a named source.",
        "SUM(outlets.reach_value) over distinct outlets with a non-null reach_value.",
        "Always read with the reach coverage percentage. If 20% of outlets have a reach figure, "
        "this is 20% of an answer, not a total.",
    ),
    Definition(
        "reach_coverage", "Outlets with a known reach value", "%",
        "What share of the outlets carrying this campaign have an audience figure from a named "
        "source. This is the honesty check on total reach.",
        "Outlets with a non-null reach_value divided by all outlets carrying the campaign.",
    ),
    # --- Velocity --------------------------------------------------------------------------
    Definition(
        "peak_day", "Peak day", "day index",
        "The day, counted from publication, on which the most articles appeared.",
        "The earliest day_index holding the maximum daily article count. Ties go to the earlier day.",
    ),
    Definition(
        "peak_volume", "Peak day volume", "articles",
        "How many articles appeared on the busiest day.",
        "The maximum daily article count.",
    ),
    Definition(
        "days_to_peak", "Days to peak", "days",
        "How long the story took to reach its busiest day. Zero means it peaked on publication day.",
        "The peak day index itself.",
    ),
    Definition(
        "half_life_days", "Half-life", "days",
        "How many days after the peak it took for daily volume to fall below a tenth of the peak "
        "and stay there — a measure of how quickly the story burned out.",
        "Days from the peak day to the first day whose 3-day trailing mean volume is below 10% of "
        "the peak volume.",
        "Null while a campaign is still running or has not yet decayed. A null is not a zero.",
    ),
    Definition(
        "days_to_90pct", "Days to 90% of volume", "days",
        "How long it took to accumulate nine tenths of all the coverage the campaign ever got.",
        "The first day index at which cumulative articles reach 90% of the campaign total.",
        "Only meaningful once a campaign has finished. Returns null for live campaigns, because "
        "the denominator is not final.",
    ),
    Definition(
        "long_tail", "Long tail", "boolean",
        "Whether the campaign was still producing coverage more than 30 days after publication.",
        "True if any article has a day index greater than 30.",
    ),
    # --- Company exposure ------------------------------------------------------------------
    Definition(
        "own_company_mentions", "Own-company mentions", "mentions",
        "How many times our own company is named, and in what capacity.",
        "Mention rows joining the own_company entity, grouped by role.",
        "Only counts articles whose body text was imported. Exports without full text cannot be "
        "scanned for mentions, and that gap is reported alongside.",
    ),
    Definition(
        "depth_score", "Exposure depth", "weighted points",
        "How central our company is to the coverage, not just how often it appears. Being the "
        "subject of a story counts far more than being listed in passing.",
        "Subject x5, named supplier x3, named buyer x3, quoted response x2, passing reference x1, "
        "summed across mentions.",
        "Never quote the total on its own. The components are what make it arguable, and they are "
        "always returned with it.",
    ),
    Definition(
        "first_mention_day", "First mention", "day index",
        "The day, counted from publication, on which our company was first named.",
        "The lowest day index among articles containing an own-company mention.",
    ),
    Definition(
        "competitor_comparison", "Named alongside peers", "mentions",
        "Whether we are named more, less, or alongside comparable companies.",
        "Mention counts per entity for own_company and competitor entities, side by side.",
    ),
    # --- Escalation ------------------------------------------------------------------------
    Definition(
        "escalation_count", "Escalations", "events",
        "How many downstream consequences the campaign has produced: legal petitions, regulatory "
        "action, customs measures, buyer statements, parliamentary questions, certifier responses.",
        "Escalation rows for the campaign within the day-index cutoff.",
        "Manually logged. A zero can mean nothing happened or that nobody logged it; the log's "
        "last-updated date is shown alongside.",
    ),
    Definition(
        "days_to_first_escalation", "Days to first escalation", "days",
        "How long after publication the first downstream consequence appeared. The sharpest early "
        "signal of whether a story is going to become a problem.",
        "Day index of the earliest escalation.",
    ),
    Definition(
        "severity_weighted_total", "Severity-weighted escalation total", "points",
        "Escalations summed by their severity, so one customs measure is not counted the same as "
        "one expression of concern.",
        "SUM(severity) across escalations, with the 1-5 anchors listed in the README.",
    ),
    Definition(
        "escalation_chain", "Escalation chain", "stages",
        "How far the campaign travelled along publication to NGO action to regulatory action to "
        "buyer action.",
        "The furthest stage reached, with the first event in each stage and the days it took.",
    ),
]

DEFINITIONS: dict[str, Definition] = {d.key: d for d in _D}


def get(key: str) -> Definition:
    if key not in DEFINITIONS:
        raise KeyError(
            f"Metric {key!r} has no plain-language definition. Add one to metrics/definitions.py "
            "before shipping the metric."
        )
    return DEFINITIONS[key]
