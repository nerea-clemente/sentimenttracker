"""HTTP API.

A thin layer over `cib.metrics`. It computes nothing: every figure the dashboard shows comes from
the same functions the CLI and the tests call, so there is exactly one definition of each metric
in the system and the frontend cannot drift from it.

Run with `cib serve` (needs the api extra).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from .. import config
from ..db import connect, query, query_one, transaction
from ..export import briefing as briefing_export
from ..export import tables as table_export
from ..metrics import definitions as metric_definitions
from ..metrics import snapshot as snapshot_module
from ..metrics.comparison import (
    METRIC_ORDER,
    PrePublicationError,
    campaign_metrics,
    compare,
    prepublication_view,
)
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
from ..repo import events as event_repo
from ..repo import imports as import_repo
from ..repo import outlets as outlet_repo
from ..watch import rules as rule_repo

app = FastAPI(
    title="Campaign Impact Benchmarker",
    version="0.1.0",
    description=(
        "Measured, traceable campaign footprints. Every metric endpoint returns the row IDs that "
        "produced each value; /evidence resolves them to source records."
    ),
)

# This is a local-first tool: the dashboard runs on the same machine, on whichever port is free.
# Any loopback origin is allowed by default rather than a hardcoded port, which would silently
# break the dashboard the first time port 3000 was taken. Set CIB_CORS_ORIGINS to pin it down.
_EXPLICIT_ORIGINS = [o.strip() for o in config.get("CIB_CORS_ORIGINS", "").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_EXPLICIT_ORIGINS,
    allow_origin_regex=(
        None if _EXPLICIT_ORIGINS else r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"
    ),
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_conn():
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def _rows(result) -> list[dict[str, Any]]:
    return [dict(r) for r in result]


# --------------------------------------------------------------------------- reference

@app.get("/api/meta", tags=["reference"])
def meta(conn: sqlite3.Connection = Depends(get_conn)):
    """Vocabularies and definitions the UI renders. Nothing is hardcoded in the frontend."""
    return {
        "metric_order": METRIC_ORDER,
        "definitions": {k: d.__dict__ for k, d in metric_definitions.DEFINITIONS.items()},
        "severity_anchors": SEVERITY_ANCHORS,
        "role_weights": ROLE_WEIGHTS,
        "outlet_tiers": list(OUTLET_TIERS),
        "escalation_types": list(ESCALATION_TYPES),
        "inbound_channels": list(INBOUND_CHANNELS),
        "mention_roles": list(MENTION_ROLES),
        "watch_rule_types": list(WATCH_RULE_TYPES),
        "last_import_at": import_repo.last_import_at(conn),
    }


# --------------------------------------------------------------------------- campaigns

@app.get("/api/campaigns", tags=["campaigns"])
def list_campaigns(conn: sqlite3.Connection = Depends(get_conn)):
    return _rows(query(conn, """
        SELECT c.*,
               (SELECT COUNT(*) FROM articles a WHERE a.campaign_id = c.id) AS article_count,
               (SELECT COUNT(DISTINCT a.outlet_id) FROM articles a WHERE a.campaign_id = c.id)
                   AS outlet_count,
               (SELECT COUNT(*) FROM escalations e WHERE e.campaign_id = c.id) AS escalation_count,
               (SELECT MAX(day_index) FROM articles a WHERE a.campaign_id = c.id)
                   AS max_day_index,
               (SELECT MAX(i.imported_at) FROM imports i JOIN articles a ON a.import_id = i.id
                 WHERE a.campaign_id = c.id) AS last_import_at
          FROM campaigns c ORDER BY c.published_at DESC NULLS LAST, c.id
    """))


@app.get("/api/campaigns/{ref}", tags=["campaigns"])
def get_campaign(ref: str, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        campaign = campaign_repo.resolve(conn, ref)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {
        **campaign.__dict__,
        "themes": campaign.theme_list,
        "is_pre_publication": campaign.is_pre_publication,
        "sources": _rows(import_repo.sources_for_campaign(conn, campaign.id)),
        "last_import_at": import_repo.last_import_at(conn, campaign.id),
        "last_snapshot_at": snapshot_module.last_snapshot_at(conn, campaign.id),
        "precedents": _rows(campaign_repo.precedents(conn, campaign.id)),
    }


@app.get("/api/campaigns/{ref}/metrics", tags=["metrics"])
def get_metrics(ref: str, at_day: int | None = Query(None, description="Day-index cutoff"),
                conn: sqlite3.Connection = Depends(get_conn)):
    """Every metric for one campaign. Returns 409, never zeros, for a pre-publication campaign."""
    try:
        return campaign_metrics(conn, ref, at_day).to_dict()
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PrePublicationError as exc:
        raise HTTPException(409, {
            "error": "pre_publication",
            "message": str(exc),
            "use_instead": f"/api/campaigns/{ref}/pre-publication",
        }) from exc


@app.get("/api/campaigns/{ref}/pre-publication", tags=["metrics"])
def get_prepublication(ref: str, conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return prepublication_view(conn, ref)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/campaigns/{ref}/series", tags=["metrics"])
def get_series(ref: str, at_day: int | None = None,
               conn: sqlite3.Connection = Depends(get_conn)):
    from ..metrics.velocity import cumulative_series

    campaign = campaign_repo.resolve(conn, ref)
    return {"campaign_slug": campaign.slug,
            "series": cumulative_series(conn, campaign.id, at_day)}


@app.get("/api/campaigns/{ref}/outlets", tags=["campaigns"])
def campaign_outlets(ref: str, at_day: int | None = None,
                     conn: sqlite3.Connection = Depends(get_conn)):
    campaign = campaign_repo.resolve(conn, ref)
    cutoff = "" if at_day is None else "AND a.day_index IS NOT NULL AND a.day_index <= ?"
    params: tuple = (campaign.id,) if at_day is None else (campaign.id, at_day)
    return _rows(query(conn, f"""
        SELECT o.id, o.name, o.domain, o.tier, o.country, o.language,
               o.reach_value, o.reach_source, o.reach_is_estimated,
               COUNT(a.id) AS articles,
               SUM(CASE WHEN a.is_original = 1 THEN 1 ELSE 0 END) AS original_articles
          FROM outlets o JOIN articles a ON a.outlet_id = o.id
         WHERE a.campaign_id = ? {cutoff}
         GROUP BY o.id ORDER BY articles DESC, o.name
    """, params))


@app.get("/api/campaigns/{ref}/clusters", tags=["campaigns"])
def campaign_clusters(ref: str, conn: sqlite3.Connection = Depends(get_conn)):
    """Syndication clusters with their members and the evidence for each membership."""
    campaign = campaign_repo.resolve(conn, ref)
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
    return clusters


@app.get("/api/campaigns/{ref}/mentions", tags=["campaigns"])
def campaign_mentions(ref: str, at_day: int | None = None,
                      conn: sqlite3.Connection = Depends(get_conn)):
    campaign = campaign_repo.resolve(conn, ref)
    cutoff = "" if at_day is None else "AND a.day_index IS NOT NULL AND a.day_index <= ?"
    params: tuple = (campaign.id,) if at_day is None else (campaign.id, at_day)
    return _rows(query(conn, f"""
        SELECT m.id, m.role, m.confidence, m.classified_by, m.evidence_sentence,
               e.name AS entity_name, e.type AS entity_type,
               a.id AS article_id, a.headline, a.url, a.day_index, a.published_at,
               o.name AS outlet
          FROM mentions m
          JOIN entities e ON e.id = m.entity_id
          JOIN articles a ON a.id = m.article_id
          JOIN outlets o ON o.id = a.outlet_id
         WHERE a.campaign_id = ? {cutoff}
         ORDER BY a.day_index, m.id
    """, params))


# --------------------------------------------------------------------------- comparison

@app.get("/api/compare", tags=["metrics"])
def get_comparison(
    campaign: list[str] = Query(..., description="2-4 campaign slugs or ids"),
    at_day: int | None = Query(None, description="Day-index cutoff applied to every campaign"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """The primary screen's data. Every campaign is truncated at the same day index."""
    try:
        return compare(conn, campaign, at_day)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# --------------------------------------------------------------------------- evidence

@app.get("/api/evidence", tags=["evidence"])
def evidence(
    table: str = Query(..., pattern="^(articles|mentions|escalations|outlets|campaigns)$"),
    ids: str = Query(..., description="Comma-separated row ids from a metric's row_ids"),
    conn: sqlite3.Connection = Depends(get_conn),
):
    """Resolve a metric's row_ids to the underlying records, with their import provenance.

    This is the drill-down behind every cell in every table. It is one generic endpoint rather
    than one per metric, which is what keeps "every number is traceable" true by construction.
    """
    try:
        id_list = [int(i) for i in ids.split(",") if i.strip()]
    except ValueError as exc:
        raise HTTPException(400, "ids must be comma-separated integers") from exc
    if not id_list:
        return {"table": table, "rows": [], "count": 0}
    if len(id_list) > 5000:
        raise HTTPException(400, "too many ids; page the request")

    placeholders = ",".join("?" * len(id_list))
    sql = {
        "articles": f"""
            SELECT a.id, a.day_index, a.published_at, a.headline, a.url, a.byline, a.country,
                   a.language, a.word_count, a.is_original, a.cluster_id,
                   o.id AS outlet_id, o.name AS outlet, o.domain, o.tier, o.country AS outlet_country,
                   o.reach_value, o.reach_source, o.reach_is_estimated,
                   i.id AS import_id, i.source AS import_source, i.file_name, i.imported_at,
                   i.created_by AS imported_by
              FROM articles a
              JOIN outlets o ON o.id = a.outlet_id
              JOIN imports i ON i.id = a.import_id
             WHERE a.id IN ({placeholders}) ORDER BY a.published_at, a.id
        """,
        "mentions": f"""
            SELECT m.id, m.role, m.confidence, m.classified_by, m.evidence_sentence, m.created_by,
                   e.name AS entity_name, e.type AS entity_type,
                   a.id AS article_id, a.headline, a.url, a.day_index, a.published_at,
                   o.name AS outlet
              FROM mentions m
              JOIN entities e ON e.id = m.entity_id
              JOIN articles a ON a.id = m.article_id
              JOIN outlets o ON o.id = a.outlet_id
             WHERE m.id IN ({placeholders}) ORDER BY a.day_index, m.id
        """,
        "escalations": f"""
            SELECT * FROM escalations WHERE id IN ({placeholders}) ORDER BY occurred_at
        """,
        "outlets": f"""
            SELECT * FROM outlets WHERE id IN ({placeholders}) ORDER BY name
        """,
        "campaigns": f"""
            SELECT * FROM campaigns WHERE id IN ({placeholders}) ORDER BY id
        """,
    }[table]
    rows = _rows(query(conn, sql, tuple(id_list)))
    return {"table": table, "count": len(rows), "requested": len(id_list), "rows": rows}


# --------------------------------------------------------------------------- escalations

@app.get("/api/campaigns/{ref}/escalations", tags=["escalations"])
def list_escalations(ref: str, conn: sqlite3.Connection = Depends(get_conn)):
    campaign = campaign_repo.resolve(conn, ref)
    return {
        "campaign_slug": campaign.slug,
        "escalations": _rows(event_repo.escalations_for(conn, campaign.id)),
        "severity_anchors": SEVERITY_ANCHORS,
        "log_note": (
            "This log is maintained by hand. An empty log means nothing has been recorded, not "
            "that nothing has happened."
        ),
    }


@app.post("/api/campaigns/{ref}/escalations", tags=["escalations"], status_code=201)
def add_escalation(ref: str, payload: dict, conn: sqlite3.Connection = Depends(get_conn)):
    campaign = campaign_repo.resolve(conn, ref)
    try:
        with transaction(conn):
            escalation_id = event_repo.add_escalation(
                conn, campaign_id=campaign.id,
                occurred_at=payload["occurred_at"],
                escalation_type=payload["escalation_type"],
                actor_name=payload["actor_name"],
                actor_type=payload.get("actor_type"),
                description=payload["description"],
                source_url=payload["source_url"],
                severity=int(payload["severity"]),
            )
    except KeyError as exc:
        raise HTTPException(400, f"missing field: {exc}") from exc
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return dict(event_repo.get_escalation(conn, escalation_id))


@app.post("/api/escalations/{escalation_id}/verify", tags=["escalations"])
def verify_escalation(escalation_id: int, payload: dict | None = None,
                      conn: sqlite3.Connection = Depends(get_conn)):
    if event_repo.get_escalation(conn, escalation_id) is None:
        raise HTTPException(404, f"No escalation {escalation_id}")
    with transaction(conn):
        event_repo.verify_escalation(conn, escalation_id, (payload or {}).get("verified_by"))
    return dict(event_repo.get_escalation(conn, escalation_id))


@app.get("/api/campaigns/{ref}/signals", tags=["escalations"])
def list_signals(ref: str, conn: sqlite3.Connection = Depends(get_conn)):
    campaign = campaign_repo.resolve(conn, ref)
    return _rows(event_repo.inbound_signals_for(conn, campaign.id))


@app.post("/api/campaigns/{ref}/signals", tags=["escalations"], status_code=201)
def add_signal(ref: str, payload: dict, conn: sqlite3.Connection = Depends(get_conn)):
    campaign = campaign_repo.resolve(conn, ref)
    try:
        with transaction(conn):
            signal_id = event_repo.add_inbound_signal(
                conn, campaign_id=campaign.id, occurred_at=payload["occurred_at"],
                channel=payload["channel"], summary=payload["summary"],
                source_ref=payload.get("source_ref"), logged_by=payload.get("logged_by"),
            )
    except KeyError as exc:
        raise HTTPException(400, f"missing field: {exc}") from exc
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return dict(query_one(conn, "SELECT * FROM inbound_signals WHERE id = ?", (signal_id,)))


# --------------------------------------------------------------------------- outlets, imports

@app.get("/api/outlets", tags=["reference"])
def list_outlets(conn: sqlite3.Connection = Depends(get_conn)):
    rows = _rows(query(conn, """
        SELECT o.*, (SELECT COUNT(*) FROM articles a WHERE a.outlet_id = o.id) AS articles
          FROM outlets o ORDER BY articles DESC, o.name
    """))
    with_reach = sum(1 for r in rows if r["reach_value"] is not None)
    return {
        "outlets": rows,
        "count": len(rows),
        "with_sourced_reach": with_reach,
        "reach_coverage_pct": round(100.0 * with_reach / len(rows), 1) if rows else None,
    }


@app.post("/api/outlets/{outlet_id}/reach", tags=["reference"])
def set_outlet_reach(outlet_id: int, payload: dict,
                     conn: sqlite3.Connection = Depends(get_conn)):
    """Record a reach figure. A named source is mandatory; an estimate must name its method."""
    if outlet_repo.get(conn, outlet_id) is None:
        raise HTTPException(404, f"No outlet {outlet_id}")
    try:
        with transaction(conn):
            outlet_repo.set_reach(
                conn, outlet_id,
                reach_value=int(payload["reach_value"]),
                reach_source=payload["reach_source"],
                is_estimated=bool(payload.get("reach_is_estimated")),
                estimation_method=payload.get("reach_estimation_method"),
                as_of=payload.get("reach_as_of"),
            )
    except KeyError as exc:
        raise HTTPException(400, f"missing field: {exc}") from exc
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return outlet_repo.get(conn, outlet_id).__dict__


@app.get("/api/imports", tags=["reference"])
def list_imports(limit: int = 50, conn: sqlite3.Connection = Depends(get_conn)):
    return _rows(import_repo.list_all(conn, limit))


# --------------------------------------------------------------------------- watch

@app.get("/api/watch/rules", tags=["watch"])
def list_watch_rules(conn: sqlite3.Connection = Depends(get_conn)):
    return _rows(query(conn, """
        SELECT r.*, c.slug AS campaign_slug,
               (SELECT COUNT(*) FROM watch_hits h WHERE h.rule_id = r.id) AS hits
          FROM watch_rules r LEFT JOIN campaigns c ON c.id = r.campaign_id ORDER BY r.id
    """))


@app.post("/api/watch/rules", tags=["watch"], status_code=201)
def create_watch_rule(payload: dict, conn: sqlite3.Connection = Depends(get_conn)):
    campaign_id = None
    if payload.get("campaign"):
        campaign_id = campaign_repo.resolve(conn, payload["campaign"]).id
    try:
        with transaction(conn):
            rule_id = rule_repo.create(
                conn, name=payload["name"], rule_type=payload["rule_type"],
                pattern=payload["pattern"], campaign_id=campaign_id,
                source_url=payload.get("source_url"),
                enabled=bool(payload.get("enabled", True)),
                promotes_to_live=bool(payload.get("promotes_to_live")),
                notes=payload.get("notes"),
            )
    except KeyError as exc:
        raise HTTPException(400, f"missing field: {exc}") from exc
    except sqlite3.IntegrityError as exc:
        raise HTTPException(400, str(exc)) from exc
    return rule_repo.get(conn, rule_id).__dict__


@app.post("/api/watch/rules/{rule_id}/enabled", tags=["watch"])
def set_rule_enabled(rule_id: int, payload: dict,
                     conn: sqlite3.Connection = Depends(get_conn)):
    if rule_repo.get(conn, rule_id) is None:
        raise HTTPException(404, f"No watch rule {rule_id}")
    with transaction(conn):
        rule_repo.set_enabled(conn, rule_id, bool(payload.get("enabled", True)))
    return rule_repo.get(conn, rule_id).__dict__


@app.get("/api/watch/hits", tags=["watch"])
def list_watch_hits(limit: int = 50, campaign: str | None = None,
                    conn: sqlite3.Connection = Depends(get_conn)):
    campaign_id = campaign_repo.resolve(conn, campaign).id if campaign else None
    return _rows(rule_repo.recent_hits(conn, limit, campaign_id))


@app.get("/api/watch/status", tags=["watch"])
def watch_status(conn: sqlite3.Connection = Depends(get_conn)):
    """Alert state for the watchlist view, including whether the poller is actually running."""
    from ..watch.poller import last_run

    run = last_run(conn)
    rules = rule_repo.list_all(conn)
    return {
        "rules_total": len(rules),
        "rules_enabled": sum(1 for r in rules if r.enabled),
        "rules_with_errors": [r.name for r in rules if r.last_error],
        "last_run": dict(run) if run else None,
        "note": (
            "No poll run recorded. Nothing is being watched: schedule `cib watch poll`, or run "
            "`cib watch run`." if run is None else ""
        ),
    }


# --------------------------------------------------------------------------- export

@app.get("/api/export/comparison.csv", tags=["export"])
def export_comparison(campaign: list[str] = Query(...), at_day: int | None = None,
                      conn: sqlite3.Connection = Depends(get_conn)):
    try:
        body = table_export.comparison_csv(conn, campaign, at_day)
    except (LookupError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return Response(body, media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="campaign-comparison.csv"'
    })


@app.get("/api/export/{ref}/{what}.csv", tags=["export"])
def export_table(ref: str, what: str, conn: sqlite3.Connection = Depends(get_conn)):
    campaign = campaign_repo.resolve(conn, ref)
    builders = {
        "articles": table_export.articles_csv,
        "escalations": table_export.escalations_csv,
        "mentions": table_export.mentions_csv,
    }
    if what not in builders:
        raise HTTPException(404, f"No such export: {what}")
    return Response(builders[what](conn, campaign.id), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{campaign.slug}-{what}.csv"'
    })


@app.get("/api/export/briefing", tags=["export"])
def export_briefing(campaign: list[str] = Query(...), at_day: int | None = None,
                    format: str = Query("html", pattern="^(html|markdown)$"),
                    conn: sqlite3.Connection = Depends(get_conn)):
    try:
        data = briefing_export.build(conn, campaign, at_day)
    except (LookupError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if format == "markdown":
        return Response(briefing_export.to_markdown(data), media_type="text/markdown")
    return Response(briefing_export.to_html(data), media_type="text/html")


@app.get("/api/health", tags=["reference"])
def health(conn: sqlite3.Connection = Depends(get_conn)):
    return {
        "status": "ok",
        "db_path": str(config.db_path()),
        "campaigns": query_one(conn, "SELECT COUNT(*) AS n FROM campaigns")["n"],
        "articles": query_one(conn, "SELECT COUNT(*) AS n FROM articles")["n"],
    }
