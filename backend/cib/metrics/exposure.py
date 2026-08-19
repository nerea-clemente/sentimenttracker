"""Company exposure: how often and how centrally our own company appears, and against whom."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict

from ..models import ROLE_WEIGHTS
from ..repo import entities as entity_repo
from . import selectors
from .result import MetricValue, metric, unavailable


def compute(conn: sqlite3.Connection, campaign_id: int,
            at_day_index: int | None = None) -> list[MetricValue]:
    own = entity_repo.own_company(conn)
    out: list[MetricValue] = []

    if own is None:
        reason = (
            "No entity of type 'own_company' is configured, so there is nothing to measure "
            "exposure for. Create one with `cib entity add --type own_company`."
        )
        return [
            unavailable("own_company_mentions", reason),
            unavailable("depth_score", reason),
            unavailable("first_mention_day", reason),
            unavailable("competitor_comparison", reason),
        ]

    with_body, total_articles = selectors.articles_with_body(conn, campaign_id)
    scan_caveats = []
    if total_articles and with_body < total_articles:
        scan_caveats.append(
            f"Only {with_body} of {total_articles} articles have body text imported. Mentions "
            "can only be detected in articles whose full text is present, so this is a floor."
        )
    if total_articles == 0:
        scan_caveats.append("No articles imported for this campaign.")

    mentions = [m for m in selectors.own_company_mentions(conn, campaign_id, at_day_index)
                if int(m["entity_id"]) == own.id]

    by_role = Counter(m["role"] for m in mentions)
    role_rows: dict[str, list[int]] = defaultdict(list)
    for m in mentions:
        role_rows[m["role"]].append(int(m["id"]))

    out.append(metric(
        "own_company_mentions", len(mentions), row_table="mentions",
        row_ids=[int(m["id"]) for m in mentions],
        basis={
            "entity": own.name,
            "by_role": dict(by_role),
            "row_ids_by_role": dict(role_rows),
            "articles_with_body_text": with_body,
            "articles_total": total_articles,
            "articles_mentioning": len({int(m["article_id"]) for m in mentions}),
        },
        caveats=scan_caveats,
    ))

    # Depth score. Returned with its components always — the total on its own is not arguable,
    # and "we scored 34" is exactly the kind of number this tool exists to replace.
    components = {role: {"count": by_role.get(role, 0), "weight": weight,
                         "points": by_role.get(role, 0) * weight}
                  for role, weight in ROLE_WEIGHTS.items()}
    weighted_total = sum(c["points"] for c in components.values())
    out.append(metric(
        "depth_score", weighted_total, row_table="mentions",
        row_ids=[int(m["id"]) for m in mentions],
        basis={"components": components, "weights": dict(ROLE_WEIGHTS), "entity": own.name},
        caveats=[
            *scan_caveats,
            "The weighted total is only meaningful alongside its components; both are returned "
            "together and the API has no endpoint that returns the total alone.",
        ],
    ))

    dated = [m for m in mentions if m["day_index"] is not None]
    if dated:
        first = min(dated, key=lambda m: int(m["day_index"]))
        out.append(metric(
            "first_mention_day", int(first["day_index"]), row_table="mentions",
            row_ids=[int(first["id"])],
            basis={
                "article_id": int(first["article_id"]),
                "headline": first["headline"],
                "url": first["url"],
                "published_at": first["published_at"],
                "role": first["role"],
                "evidence_sentence": first["evidence_sentence"],
            },
        ))
    else:
        out.append(unavailable(
            "first_mention_day",
            "No dated mention of the own-company entity in the imported articles.",
            basis=({"note": scan_caveats[0]} if scan_caveats else {}),
        ))

    # Competitor comparison: are we named more, less, or alongside peers.
    competitor_mentions = selectors.mentions_for_entity_type(
        conn, campaign_id, "competitor", at_day_index
    )
    per_entity: dict[str, dict] = {
        own.name: {
            "entity_type": "own_company",
            "mentions": len(mentions),
            "articles": len({int(m["article_id"]) for m in mentions}),
            "row_ids": [int(m["id"]) for m in mentions],
        }
    }
    for m in competitor_mentions:
        e = per_entity.setdefault(m["entity_name"], {
            "entity_type": "competitor", "mentions": 0, "articles": 0, "row_ids": [],
        })
        e["mentions"] += 1
        e["row_ids"].append(int(m["id"]))
    for name, data in per_entity.items():
        if data["entity_type"] == "competitor":
            data["articles"] = len({
                int(m["article_id"]) for m in competitor_mentions if m["entity_name"] == name
            })

    ranked = sorted(per_entity.items(), key=lambda kv: -kv[1]["mentions"])
    own_rank = next((i + 1 for i, (n, _) in enumerate(ranked) if n == own.name), None)
    standing = "no competitors configured" if len(ranked) == 1 else (
        "named most" if own_rank == 1 else
        "named least" if own_rank == len(ranked) else "named alongside peers"
    )
    out.append(metric(
        "competitor_comparison", standing, row_table="mentions",
        row_ids=[*[int(m["id"]) for m in mentions],
                 *[int(m["id"]) for m in competitor_mentions]],
        basis={"per_entity": per_entity, "own_rank": own_rank, "entities_compared": len(ranked)},
        caveats=[
            *scan_caveats,
            *(["No competitor entities are configured, so this comparison has nothing to "
               "compare against."] if len(ranked) == 1 else []),
        ],
    ))

    return out
