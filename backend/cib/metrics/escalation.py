"""Escalation metrics — the downstream consequences of a campaign.

The escalation chain is the most predictive part of the model: a story that produces a customs
measure in week two is a different animal from one that produces three weeks of trade coverage
and nothing else.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict

from ..db import query_one
from ..models import SEVERITY_ANCHORS
from ..timeutil import day_index as compute_day_index
from . import selectors
from .result import MetricValue, metric, unavailable

# The chain the tool tracks. Ordered: reaching a later stage implies more serious consequences.
CHAIN_STAGES = [
    ("publication", ()),
    ("ngo_action", ("legal_petition",)),
    ("institutional_query", ("parliamentary_question", "certifier_response")),
    ("market_action", ("retailer_statement", "buyer_statement")),
    ("regulatory_action", ("regulatory_action", "customs_measure")),
]
_STAGE_OF = {t: stage for stage, types in CHAIN_STAGES for t in types}


def compute(conn: sqlite3.Connection, campaign_id: int, published_at: str | None,
            timezone: str = "UTC", at_day_index: int | None = None) -> list[MetricValue]:
    rows = selectors.escalations(conn, campaign_id, published_at, timezone, at_day_index)
    out: list[MetricValue] = []

    log_row = query_one(
        conn,
        "SELECT MAX(created_at) AS last FROM escalations WHERE campaign_id = ?",
        (campaign_id,),
    )
    last_logged = log_row["last"] if log_row else None
    log_caveat = (
        f"Escalations are logged by hand. This log was last added to on {last_logged}."
        if last_logged else
        "Nothing has ever been logged to this campaign's escalation log, so a count of zero means "
        "'not recorded', not 'nothing happened'."
    )

    by_type = Counter(r["escalation_type"] for r in rows)
    type_rows: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        type_rows[r["escalation_type"]].append(int(r["id"]))

    verified = sum(1 for r in rows if r["verified_by"])
    out.append(metric(
        "escalation_count", len(rows), row_table="escalations",
        row_ids=[int(r["id"]) for r in rows],
        basis={
            "by_type": dict(by_type.most_common()),
            "row_ids_by_type": dict(type_rows),
            "verified": verified,
            "unverified": len(rows) - verified,
            "log_last_updated": last_logged,
        },
        caveats=[log_caveat] + ([
            f"{len(rows) - verified} of {len(rows)} escalations have not been independently "
            "verified."
        ] if len(rows) > verified else []),
    ))

    # Timeline with day indices, so escalations plot on the same axis as coverage.
    timeline = []
    for r in rows:
        di = compute_day_index(published_at, r["occurred_at"], timezone)
        timeline.append({
            "id": int(r["id"]),
            "day_index": di,
            "occurred_at": r["occurred_at"],
            "escalation_type": r["escalation_type"],
            "stage": _STAGE_OF.get(r["escalation_type"], "other"),
            "actor_name": r["actor_name"],
            "severity": int(r["severity"]),
            "severity_anchor": SEVERITY_ANCHORS[int(r["severity"])],
            "source_url": r["source_url"],
            "verified_by": r["verified_by"],
        })

    dated = [t for t in timeline if t["day_index"] is not None]
    if published_at is None:
        out.append(unavailable(
            "days_to_first_escalation",
            "The campaign has no publication date, so there is nothing to measure days from.",
        ))
    elif not dated:
        out.append(unavailable(
            "days_to_first_escalation",
            "No escalations logged for this campaign within the cutoff.",
            basis={"log_last_updated": last_logged},
        ))
    else:
        first = min(dated, key=lambda t: t["day_index"])
        out.append(metric(
            "days_to_first_escalation", first["day_index"], row_table="escalations",
            row_ids=[first["id"]],
            basis={"escalation": first},
            caveats=[log_caveat],
        ))

    severity_total = sum(int(r["severity"]) for r in rows)
    out.append(metric(
        "severity_weighted_total", severity_total, row_table="escalations",
        row_ids=[int(r["id"]) for r in rows],
        basis={
            "by_severity": dict(Counter(int(r["severity"]) for r in rows)),
            "anchors": SEVERITY_ANCHORS,
            "running_total": _running_total(timeline),
        },
        caveats=[log_caveat],
    ))

    reached = [stage for stage, _ in CHAIN_STAGES
               if (stage == "publication" and published_at)
               or any(t["stage"] == stage for t in timeline)]
    furthest = reached[-1] if reached else None
    chain = []
    for stage, types in CHAIN_STAGES:
        if stage == "publication":
            chain.append({
                "stage": stage, "reached": published_at is not None,
                "first_at": published_at, "day_index": 0 if published_at else None,
                "escalation_ids": [],
            })
            continue
        events = [t for t in timeline if t["stage"] == stage]
        first = min(events, key=lambda t: t["occurred_at"]) if events else None
        chain.append({
            "stage": stage,
            "types": list(types),
            "reached": bool(events),
            "first_at": first["occurred_at"] if first else None,
            "day_index": first["day_index"] if first else None,
            "escalation_ids": [e["id"] for e in events],
        })
    # When nothing has escalated, the chain's only claim is "it published and went no further".
    # That claim traces to the campaign row's published_at, not to the empty escalation log, so
    # the drill-down points there rather than at nothing.
    if rows:
        chain_table, chain_ids = "escalations", [int(r["id"]) for r in rows]
    else:
        chain_table, chain_ids = "campaigns", [campaign_id]
    out.append(metric(
        "escalation_chain", furthest, row_table=chain_table, row_ids=chain_ids,
        basis={"chain": chain, "timeline": timeline,
               "traced_to": ("escalation log" if rows else
                             "the campaign's publication date; the escalation log is empty")},
        caveats=[log_caveat],
    ))

    return out


def _running_total(timeline: list[dict]) -> list[dict]:
    running = 0
    series = []
    for event in sorted(timeline, key=lambda t: (t["day_index"] is None, t["day_index"],
                                                 t["occurred_at"])):
        running += event["severity"]
        series.append({
            "day_index": event["day_index"],
            "occurred_at": event["occurred_at"],
            "severity": event["severity"],
            "running_total": running,
            "escalation_id": event["id"],
        })
    return series
