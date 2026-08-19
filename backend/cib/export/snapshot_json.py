"""SQLite → dashboard JSON, for the static GitHub Pages build.

    python -m cib.cli export snapshot
    python -m cib.cli export snapshot --out web/src/lib/seed.json

The dashboard normally talks to the FastAPI layer, which calls `cib.metrics`. GitHub Pages serves
static files only, so there is no Python process to call. This writes every figure the dashboard
needs into one JSON file that Next.js imports at build time.

The critical property is preserved: **every number in this file is computed by `cib.metrics`**,
the same functions the CLI, the API and the tests use. The static frontend selects from what is
baked here; it does not compute. That is why comparison payloads are baked per campaign-subset and
per cutoff rather than being assembled client-side — assembling them in TypeScript would put
metric logic in the frontend, which is the thing the architecture exists to prevent.

Comparison columns reference the per-campaign metric sets by (slug, cutoff) instead of repeating
them, because `row_ids` on a large campaign dominates the file size otherwise.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

from ..actor import actor, now_utc
from ..config import REPO_ROOT
from ..db import connect, query, query_one
from ..metrics import METRIC_ORDER
from ..metrics import definitions as metric_definitions
from ..metrics import snapshot as snapshot_module
from ..metrics.comparison import (
    campaign_metrics,
    compare,
    max_observed_day_index,
    prepublication_view,
)
from ..metrics.velocity import cumulative_series
from ..migrations.runner import migrate
from ..models import (
    ESCALATION_TYPES,
    INBOUND_CHANNELS,
    MENTION_ROLES,
    OUTLET_TIERS,
    ROLE_WEIGHTS,
    SEVERITY_ANCHORS,
    WATCH_RULE_TYPES,
)
from ..repo import campaigns as campaign_repo
from ..repo import imports as import_repo
from ..watch import rules as rule_repo

log = logging.getLogger("cib.export.snapshot")

DEFAULT_OUT = "web/src/lib/seed.json"
DEFAULT_ASSETS_DIR = "web/public/exports"

# Day-index cutoffs baked into the snapshot. The static dashboard's cutoff selector offers exactly
# these, because a cutoff nobody baked is a cutoff nobody can compute without a Python process.
DEFAULT_CUTOFFS: tuple[int, ...] = (7, 14, 30, 90)

# Subset sizes to bake comparisons for. Four campaigns is the comparison view's own cap; baking
# every quad gets expensive above a handful of campaigns, so quads are baked only for small sets.
MAX_QUAD_CAMPAIGNS = 5


def _rows(result) -> list[dict[str, Any]]:
    return [dict(r) for r in result]


# Fields on every MetricValue that are identical for a given metric key everywhere it appears.
# They live once in `meta.definitions`; repeating them across 23 metrics x N cutoffs x M campaigns
# is what turns a small database into a multi-megabyte snapshot. The frontend joins them back by
# key — a lookup, not a computation.
_DEDUPED_METRIC_FIELDS = ("label", "unit", "definition")


def _slim(metric_set: dict[str, Any]) -> dict[str, Any]:
    """Drop per-metric fields that are constant per metric key and already in `meta`."""
    for metric in metric_set.get("metrics", {}).values():
        for field in _DEDUPED_METRIC_FIELDS:
            metric.pop(field, None)
        # `caveats` on a MetricValue is the run-specific caveats plus the definition's static
        # caveat appended. Keep only the run-specific ones; the static one comes from `meta`.
        static = metric_definitions.DEFINITIONS[metric["key"]].caveat
        metric["caveats"] = [c for c in metric.get("caveats", []) if c and c != static]
    return metric_set


def cutoff_key(cutoff: int | None) -> str:
    return "lifetime" if cutoff is None else str(cutoff)


def _campaign_summaries(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _rows(query(conn, """
        SELECT c.id, c.name, c.slug, c.publisher_org, c.campaign_type, c.status,
               c.published_at, c.first_signal_at, c.timezone, c.themes, c.notes,
               (SELECT COUNT(*) FROM articles a WHERE a.campaign_id = c.id) AS article_count,
               (SELECT COUNT(DISTINCT a.outlet_id) FROM articles a WHERE a.campaign_id = c.id)
                   AS outlet_count,
               (SELECT COUNT(*) FROM escalations e WHERE e.campaign_id = c.id) AS escalation_count,
               (SELECT MAX(day_index) FROM articles a WHERE a.campaign_id = c.id)
                   AS max_day_index,
               (SELECT MAX(i.imported_at) FROM imports i JOIN articles a ON a.import_id = i.id
                 WHERE a.campaign_id = c.id) AS last_import_at
          FROM campaigns c ORDER BY c.published_at DESC, c.id
    """))


def _detail(conn: sqlite3.Connection, campaign, cutoffs: tuple[int, ...]) -> dict[str, Any]:
    """Everything the campaign detail view needs, plus metric sets at every baked cutoff."""
    detail: dict[str, Any] = {
        "campaign": {
            **campaign.__dict__,
            "themes": campaign.theme_list,
            "is_pre_publication": campaign.is_pre_publication,
        },
        "sources": _rows(import_repo.sources_for_campaign(conn, campaign.id)),
        "last_import_at": import_repo.last_import_at(conn, campaign.id),
        "last_snapshot_at": snapshot_module.last_snapshot_at(conn, campaign.id),
        "precedents": _rows(campaign_repo.precedents(conn, campaign.id)),
        "escalations": _rows(query(
            conn, "SELECT * FROM escalations WHERE campaign_id = ? ORDER BY occurred_at",
            (campaign.id,))),
        "signals": _rows(query(
            conn, "SELECT * FROM inbound_signals WHERE campaign_id = ? ORDER BY occurred_at",
            (campaign.id,))),
        "max_observed_day_index": max_observed_day_index(conn, campaign.id),
    }

    if campaign.published_at is None:
        detail["pre_publication"] = prepublication_view(conn, campaign.id)
        detail["metrics_by_cutoff"] = {}
        detail["series_by_cutoff"] = {}
        detail["outlets"] = []
        detail["clusters"] = []
        detail["mentions"] = []
        return detail

    detail["pre_publication"] = None
    observed = detail["max_observed_day_index"]

    # Bake the lifetime view plus any cutoff that actually falls inside the campaign's run. A
    # cutoff beyond the last day of coverage would produce a copy of the lifetime figures under a
    # misleading label.
    applicable = [None] + [c for c in cutoffs if observed is not None and c <= observed]
    detail["cutoffs"] = [cutoff_key(c) for c in applicable]
    detail["metrics_by_cutoff"] = {
        cutoff_key(c): _slim(campaign_metrics(conn, campaign.id, c).to_dict())
        for c in applicable
    }
    detail["series_by_cutoff"] = {
        cutoff_key(c): cumulative_series(conn, campaign.id, c) for c in applicable
    }

    detail["outlets"] = _rows(query(conn, """
        SELECT o.id, o.name, o.domain, o.tier, o.country, o.language,
               o.reach_value, o.reach_source, o.reach_is_estimated,
               COUNT(a.id) AS articles,
               SUM(CASE WHEN a.is_original = 1 THEN 1 ELSE 0 END) AS original_articles
          FROM outlets o JOIN articles a ON a.outlet_id = o.id
         WHERE a.campaign_id = ?
         GROUP BY o.id ORDER BY articles DESC, o.name
    """, (campaign.id,)))

    clusters = _rows(query(conn, """
        SELECT c.*, a.headline AS representative_headline
          FROM clusters c LEFT JOIN articles a ON a.id = c.representative_article_id
         WHERE c.campaign_id = ? ORDER BY c.member_count DESC, c.id
    """, (campaign.id,)))
    for cluster in clusters:
        cluster["members"] = _rows(query(conn, """
            SELECT cm.article_id, cm.method, cm.similarity, cm.matched_against_article_id,
                   a.headline, a.published_at, a.url, a.is_original, o.name AS outlet
              FROM cluster_members cm
              JOIN articles a ON a.id = cm.article_id
              JOIN outlets o ON o.id = a.outlet_id
             WHERE cm.cluster_id = ? ORDER BY a.published_at
        """, (cluster["id"],)))
    detail["clusters"] = clusters

    detail["mentions"] = _rows(query(conn, """
        SELECT m.id, m.role, m.confidence, m.classified_by, m.evidence_sentence,
               e.name AS entity_name, e.type AS entity_type,
               a.id AS article_id, a.headline, a.url, a.day_index, a.published_at,
               o.name AS outlet
          FROM mentions m
          JOIN entities e ON e.id = m.entity_id
          JOIN articles a ON a.id = m.article_id
          JOIN outlets o ON o.id = a.outlet_id
         WHERE a.campaign_id = ?
         ORDER BY a.day_index, m.id
    """, (campaign.id,)))

    return detail


# Traceability is the point of the tool, so the drill-down must work in the static build too.
# These are the queries /api/evidence uses, so a row looks identical whether it came from the live
# API or the snapshot. Article body text is deliberately not included: it is the largest column in
# the database and nothing in the UI displays it.
_EVIDENCE_QUERIES = {
    "articles": """
        SELECT a.id, a.day_index, a.published_at, a.headline, a.url, a.byline, a.country,
               a.language, a.word_count, a.is_original, a.cluster_id, a.campaign_id,
               o.id AS outlet_id, o.name AS outlet, o.domain, o.tier,
               o.country AS outlet_country, o.reach_value, o.reach_source, o.reach_is_estimated,
               i.id AS import_id, i.source AS import_source, i.file_name, i.imported_at,
               i.created_by AS imported_by
          FROM articles a
          JOIN outlets o ON o.id = a.outlet_id
          JOIN imports i ON i.id = a.import_id
         ORDER BY a.published_at, a.id
    """,
    "mentions": """
        SELECT m.id, m.role, m.confidence, m.classified_by, m.evidence_sentence, m.created_by,
               e.name AS entity_name, e.type AS entity_type,
               a.id AS article_id, a.headline, a.url, a.day_index, a.published_at,
               o.name AS outlet
          FROM mentions m
          JOIN entities e ON e.id = m.entity_id
          JOIN articles a ON a.id = m.article_id
          JOIN outlets o ON o.id = a.outlet_id
         ORDER BY a.day_index, m.id
    """,
    "escalations": "SELECT * FROM escalations ORDER BY occurred_at",
    "outlets": "SELECT * FROM outlets ORDER BY name",
    "campaigns": "SELECT * FROM campaigns ORDER BY id",
}


def _evidence_index(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Every row a metric's `row_ids` can point at, keyed by id."""
    return {
        table: {str(row["id"]): dict(row) for row in query(conn, sql)}
        for table, sql in _EVIDENCE_QUERIES.items()
    }


def comparison_key(slugs: list[str], cutoff: int | None) -> str:
    return "|".join([*sorted(slugs), cutoff_key(cutoff)])


def _comparisons(conn: sqlite3.Connection, published: list, cutoffs: tuple[int, ...],
                 warnings: list[str], empty_slugs: set[str]) -> dict[str, Any]:
    """Bake a comparison payload per campaign subset per cutoff.

    Columns are stored as (slug, cutoff) references into `campaign_detail`, so a campaign's
    `row_ids` are written once rather than once per comparison it appears in.
    """
    slugs = [c.slug for c in published]
    sizes = [2, 3] + ([4] if len(slugs) <= MAX_QUAD_CAMPAIGNS else [])
    if len(slugs) > MAX_QUAD_CAMPAIGNS:
        warnings.append(
            f"{len(slugs)} published campaigns: four-way comparisons were not baked into the "
            f"snapshot (only two- and three-way). Raise MAX_QUAD_CAMPAIGNS or run the live API "
            "to compare four at once."
        )

    baked: dict[str, Any] = {}
    for size in sizes:
        for subset in itertools.combinations(slugs, size):
            for cutoff in (None, *cutoffs):
                try:
                    result = compare(conn, list(subset), cutoff)
                except (ValueError, LookupError) as exc:  # pragma: no cover - defensive
                    warnings.append(f"comparison {subset} at {cutoff_key(cutoff)}: {exc}")
                    continue
                resolved = result["at_day_index"]
                baked[comparison_key(list(subset), cutoff)] = {
                    "requested_cutoff": cutoff_key(cutoff),
                    "at_day_index": resolved,
                    "cutoff_was_explicit": result["cutoff_was_explicit"],
                    "caveats": result["caveats"],
                    # Reference, not a copy: the metric sets live in campaign_detail.
                    "columns": [
                        {
                            "slug": column["campaign_slug"],
                            # A campaign with no dated coverage has identical figures at every
                            # cutoff, so it is baked once under "lifetime" and pointed at here.
                            "cutoff": ("lifetime" if column["campaign_slug"] in empty_slugs
                                       else cutoff_key(resolved)),
                        }
                        for column in result["campaigns"]
                    ],
                    "pre_publication_slugs": [
                        v["campaign"]["slug"] for v in result["pre_publication"]
                    ],
                    # No series here: the chart reads `campaign_detail[slug].series_by_cutoff`
                    # under the same (slug, cutoff) key the columns already carry. Repeating it
                    # per comparison multiplied the file size by the number of subsets.
                }
    return baked


def build_payload(conn: sqlite3.Connection,
                  cutoffs: tuple[int, ...] = DEFAULT_CUTOFFS,
                  extra_warnings: list[str] | None = None) -> dict[str, Any]:
    # Warnings surface as a banner on every page of the static build. That is the mechanism for
    # saying "this is demo data" loudly enough that nobody quotes it, so it is deliberately not
    # something the frontend can style away or collapse.
    warnings: list[str] = list(extra_warnings or [])
    campaigns = campaign_repo.list_all(conn)
    published = [c for c in campaigns if c.published_at is not None]

    detail = {c.slug: _detail(conn, c, cutoffs) for c in campaigns}
    empty_slugs = {
        slug for slug, d in detail.items()
        if d["max_observed_day_index"] is None and d["pre_publication"] is None
    }

    # Any campaign column referenced by a comparison must exist in campaign_detail at that
    # cutoff, or the static dashboard would resolve a reference to nothing.
    comparisons = _comparisons(conn, published, cutoffs, warnings, empty_slugs)
    for key, payload in comparisons.items():
        for column in payload["columns"]:
            if column["cutoff"] not in detail[column["slug"]]["metrics_by_cutoff"]:
                # The resolved cutoff was derived rather than one of ours: bake that one too.
                resolved = None if column["cutoff"] == "lifetime" else int(column["cutoff"])
                target = detail[column["slug"]]
                cid = next(c.id for c in campaigns if c.slug == column["slug"])
                target["metrics_by_cutoff"][column["cutoff"]] = _slim(
                    campaign_metrics(conn, cid, resolved).to_dict()
                )
                target["series_by_cutoff"][column["cutoff"]] = cumulative_series(
                    conn, cid, resolved
                )
                target.setdefault("cutoffs", []).append(column["cutoff"])
        del key

    outlets = _rows(query(conn, """
        SELECT o.*, (SELECT COUNT(*) FROM articles a WHERE a.outlet_id = o.id) AS articles
          FROM outlets o ORDER BY articles DESC, o.name
    """))
    with_reach = sum(1 for o in outlets if o["reach_value"] is not None)

    watch_rules = _rows(query(conn, """
        SELECT r.*, c.slug AS campaign_slug,
               (SELECT COUNT(*) FROM watch_hits h WHERE h.rule_id = r.id) AS hits
          FROM watch_rules r LEFT JOIN campaigns c ON c.id = r.campaign_id ORDER BY r.id
    """))
    last_run = query_one(conn, "SELECT * FROM watch_runs ORDER BY id DESC LIMIT 1")

    total_articles = query_one(conn, "SELECT COUNT(*) AS n FROM articles")["n"]
    if total_articles == 0:
        warnings.append(
            "No articles are imported in this database, so every footprint figure in this "
            "snapshot is an empty dataset rather than a measurement of low coverage."
        )

    return {
        "generated_at": now_utc(),
        "generated_by": actor(),
        "mode": "snapshot",
        "warnings": warnings,
        "meta": {
            "metric_order": METRIC_ORDER,
            "definitions": {k: d.__dict__ for k, d in metric_definitions.DEFINITIONS.items()},
            "severity_anchors": {str(k): v for k, v in SEVERITY_ANCHORS.items()},
            "role_weights": dict(ROLE_WEIGHTS),
            "outlet_tiers": list(OUTLET_TIERS),
            "escalation_types": list(ESCALATION_TYPES),
            "inbound_channels": list(INBOUND_CHANNELS),
            "mention_roles": list(MENTION_ROLES),
            "watch_rule_types": list(WATCH_RULE_TYPES),
            "last_import_at": import_repo.last_import_at(conn),
        },
        "stats": {
            "campaigns": len(campaigns),
            "campaigns_published": len(published),
            "campaigns_pre_publication": len(campaigns) - len(published),
            "articles": int(total_articles),
            "outlets": len(outlets),
            "outlets_with_sourced_reach": with_reach,
            "escalations": int(query_one(conn, "SELECT COUNT(*) AS n FROM escalations")["n"]),
            "mentions": int(query_one(conn, "SELECT COUNT(*) AS n FROM mentions")["n"]),
            "comparisons_baked": len(comparisons),
        },
        "cutoffs": [cutoff_key(c) for c in (None, *cutoffs)],
        "evidence": _evidence_index(conn),
        "campaigns": _campaign_summaries(conn),
        "campaign_detail": detail,
        "comparisons": comparisons,
        "outlets": {
            "outlets": outlets,
            "count": len(outlets),
            "with_sourced_reach": with_reach,
            "reach_coverage_pct": round(100.0 * with_reach / len(outlets), 1) if outlets else None,
        },
        "imports": _rows(import_repo.list_all(conn, 200)),
        "watch": {
            "rules": watch_rules,
            "hits": _rows(rule_repo.recent_hits(conn, 100)),
            "status": {
                "rules_total": len(watch_rules),
                "rules_enabled": sum(1 for r in watch_rules if r["enabled"]),
                "rules_with_errors": [r["name"] for r in watch_rules if r["last_error"]],
                "last_run": dict(last_run) if last_run else None,
                "note": (
                    "No poll run recorded. Nothing is being watched: schedule `cib watch poll`, "
                    "or run `cib watch run`." if last_run is None else ""
                ),
            },
        },
    }


def write_static_assets(conn: sqlite3.Connection, assets_dir: Path) -> list[str]:
    """Write the downloadable exports as real files, for the static build.

    In live mode these come from `/api/export/...`. On GitHub Pages there is no API to serve
    them, and an export button pointing at a dead localhost URL is worse than no button — so the
    briefing and the per-campaign CSVs are generated ahead of time and shipped as static files.

    Comparison exports are not baked: they depend on which campaigns the reader selects, and
    every subset at every cutoff is far more files than it is worth. The UI says so in static mode
    rather than offering a link that fails.
    """
    from . import briefing as briefing_export
    from . import tables as table_export

    written: list[str] = []
    assets_dir.mkdir(parents=True, exist_ok=True)
    for campaign in campaign_repo.list_all(conn):
        slug = campaign.slug
        data = briefing_export.build(conn, [campaign.id])
        (assets_dir / f"{slug}-briefing.html").write_text(
            briefing_export.to_html(data), encoding="utf-8"
        )
        written.append(f"{slug}-briefing.html")

        if campaign.published_at is None:
            continue
        for name, builder in (
            ("articles", table_export.articles_csv),
            ("escalations", table_export.escalations_csv),
            ("mentions", table_export.mentions_csv),
        ):
            (assets_dir / f"{slug}-{name}.csv").write_text(
                builder(conn, campaign.id), encoding="utf-8"
            )
            written.append(f"{slug}-{name}.csv")
    return written


def write(conn: sqlite3.Connection, out_path: Path,
          cutoffs: tuple[int, ...] = DEFAULT_CUTOFFS,
          assets_dir: Path | None = None,
          extra_warnings: list[str] | None = None) -> dict[str, Any]:
    payload = build_payload(conn, cutoffs, extra_warnings)
    if assets_dir is not None:
        payload["static_assets"] = write_static_assets(conn, assets_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cib.export.snapshot")
    parser.add_argument("--out", default=DEFAULT_OUT,
                        help=f"Where to write the snapshot (default: {DEFAULT_OUT})")
    parser.add_argument("--db", help="SQLite path (default: CIB_DB_PATH)")
    parser.add_argument("--cutoff", type=int, action="append",
                        help="Day-index cutoff to bake; repeatable. "
                             f"Default: {', '.join(map(str, DEFAULT_CUTOFFS))}")
    parser.add_argument("--assets-dir", default=DEFAULT_ASSETS_DIR,
                        help="Where to write briefing HTML and CSV exports for the static build "
                             f"(default: {DEFAULT_ASSETS_DIR}). Pass '' to skip.")
    parser.add_argument("--warn", action="append",
                        help="Extra warning shown on every page of the static build; repeatable. "
                             "Use it to label a snapshot built from demo data.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = REPO_ROOT / out_path

    assets_dir = None
    if args.assets_dir:
        assets_dir = Path(args.assets_dir)
        if not assets_dir.is_absolute():
            assets_dir = REPO_ROOT / assets_dir

    conn = connect(args.db)
    try:
        migrate(conn)
        payload = write(conn, out_path,
                        tuple(args.cutoff) if args.cutoff else DEFAULT_CUTOFFS,
                        assets_dir, args.warn)
    finally:
        conn.close()

    stats = payload["stats"]
    log.info(
        "wrote %s — campaigns=%d articles=%d comparisons=%d (%.1f KB)",
        out_path.name, stats["campaigns"], stats["articles"], stats["comparisons_baked"],
        out_path.stat().st_size / 1024,
    )
    for warning in payload["warnings"]:
        log.warning(warning)
    return 0


if __name__ == "__main__":
    sys.exit(main())
