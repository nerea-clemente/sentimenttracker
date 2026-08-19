"""Command line interface.

argparse only, so the core tool needs no third-party install. Every subcommand that writes takes
`--as NAME` to record who did it; without it the OS user is recorded.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import config, dedup
from . import entities as entity_matcher
from . import seed as seed_module
from .actor import set_actor
from .db import connect, query, transaction
from .metrics import METRIC_ORDER, PrePublicationError
from .metrics import definitions as metric_definitions
from .metrics import snapshot as snapshot_module
from .metrics.comparison import campaign_metrics, compare, prepublication_view
from .migrations.runner import migrate
from .models import SEVERITY_ANCHORS
from .repo import campaigns as campaign_repo
from .repo import entities as entity_repo
from .repo import events as event_repo
from .repo import imports as import_repo
from .repo import outlets as outlet_repo


def _open(args) -> sqlite3.Connection:
    conn = connect(args.db)
    migrate(conn)
    return conn


def _print_json(payload) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _parse_map(pairs: list[str] | None) -> dict[str, str] | None:
    if not pairs:
        return None
    mapping = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"--map expects field=column, got {pair!r}")
        field, column = pair.split("=", 1)
        mapping[field.strip()] = column.strip()
    return mapping


# --------------------------------------------------------------------------- init / seed

def cmd_init(args) -> int:
    conn = connect(args.db)
    applied = migrate(conn, verbose=True)
    print(f"Database ready at {args.db or config.db_path()}")
    print(f"Migrations applied this run: {', '.join(applied) or 'none (already up to date)'}")
    return 0


def cmd_seed(args) -> int:
    conn = _open(args)
    with transaction(conn):
        created = seed_module.run(conn, force=args.force)
    if not any(created.values()):
        print("Seed records already present. Nothing created. Use --force to recreate campaigns.")
    else:
        _print_json(created)
    print(
        "\nSeeded records are placeholders. No article data was invented: the two archived "
        "campaigns are empty until real exports are imported, and the watch rules are disabled "
        "because they point at placeholder domains."
    )
    return 0


# --------------------------------------------------------------------------- campaigns

def cmd_campaign_list(args) -> int:
    conn = _open(args)
    rows = query(conn, """
        SELECT c.id, c.slug, c.name, c.status, c.published_at, c.publisher_org,
               (SELECT COUNT(*) FROM articles a WHERE a.campaign_id = c.id) AS articles,
               (SELECT COUNT(*) FROM escalations e WHERE e.campaign_id = c.id) AS escalations
          FROM campaigns c ORDER BY c.id
    """)
    if args.json:
        _print_json([dict(r) for r in rows])
        return 0
    if not rows:
        print("No campaigns. Run `cib seed` to create the three starter records.")
        return 0
    print(f"{'id':>3}  {'slug':<52} {'status':<16} {'published':<12} {'arts':>5} {'esc':>4}")
    for r in rows:
        print(f"{r['id']:>3}  {r['slug'][:52]:<52} {r['status']:<16} "
              f"{(r['published_at'] or '—')[:10]:<12} {r['articles']:>5} {r['escalations']:>4}")
    return 0


def cmd_campaign_add(args) -> int:
    conn = _open(args)
    with transaction(conn):
        campaign_id = campaign_repo.create(
            conn, name=args.name, publisher_org=args.publisher,
            campaign_type=args.type, status=args.status,
            published_at=args.published_at, first_signal_at=args.first_signal_at,
            timezone=args.timezone, themes=args.theme or [], notes=args.notes, slug=args.slug,
        )
    campaign = campaign_repo.get(conn, campaign_id)
    print(f"Created campaign #{campaign_id} ({campaign.slug})")
    return 0


def cmd_campaign_set_published(args) -> int:
    conn = _open(args)
    campaign = campaign_repo.resolve(conn, args.campaign)
    with transaction(conn):
        campaign_repo.set_published(conn, campaign.id, args.published_at, status=args.status)
        from .repo import articles as article_repo
        updated = article_repo.recompute_day_index(
            conn, campaign.id, args.published_at, campaign.timezone
        )
        written = snapshot_module.rebuild(conn, campaign.id)
    print(f"{campaign.slug}: published_at = {args.published_at}, status = {args.status}")
    print(f"Recomputed day_index on {updated} article(s); wrote {written} daily snapshot row(s).")
    return 0


def cmd_campaign_precedent(args) -> int:
    conn = _open(args)
    campaign = campaign_repo.resolve(conn, args.campaign)
    precedent = campaign_repo.resolve(conn, args.precedent)
    with transaction(conn):
        campaign_repo.add_precedent(conn, campaign.id, precedent.id, args.rationale)
    print(f"Linked {precedent.slug} as publisher precedent for {campaign.slug}")
    return 0


# --------------------------------------------------------------------------- import

def cmd_import_inspect(args) -> int:
    if args.source == "infomedia":
        from .ingest import infomedia
        _print_json(infomedia.inspect(args.file, args.encoding))
    elif args.source == "factiva":
        from .ingest import factiva
        _print_json(factiva.inspect(args.file, args.encoding))
    elif args.source == "nexis":
        from .ingest import nexis
        _print_json(nexis.inspect(args.file, args.encoding))
    else:
        from .ingest import csv_generic
        _print_json(csv_generic.sniff(args.file, args.encoding))
    return 0


def cmd_import_file(args) -> int:
    conn = _open(args)
    mapping = _parse_map(args.map)
    kwargs = {
        "campaign_ref": args.campaign, "path": args.file, "mapping": mapping,
        "encoding": args.encoding, "notes": args.notes,
    }
    if args.default_tier:
        kwargs["default_tier"] = args.default_tier
    if args.default_country:
        kwargs["default_country"] = args.default_country
    if args.default_language:
        kwargs["default_language"] = args.default_language

    with transaction(conn):
        if args.source == "infomedia":
            from .ingest import infomedia
            report = infomedia.import_file(conn, **kwargs)
        elif args.source == "factiva":
            from .ingest import factiva
            report = factiva.import_file(conn, **kwargs)
        elif args.source == "nexis":
            from .ingest import nexis
            report = nexis.import_file(conn, **kwargs)
        else:
            from .ingest import csv_generic
            report = csv_generic.import_file(conn, source=args.source, **kwargs)

    print(report.summary())
    if report.already_imported_as is not None and report.inserted == 0:
        print("This file was imported before and contained nothing new. No duplicates created.")
    if report.rejections:
        print(f"\nFirst {len(report.rejections)} rejected row(s):")
        for r in report.rejections[:10]:
            print(f"  row {r['row']}: {r['reason']}")

    if not args.no_cluster:
        with transaction(conn):
            cluster_report = dedup.cluster_campaign(conn, campaign_repo.resolve(conn, args.campaign).id)
        print(f"\nClustering: {cluster_report.clusters_created} cluster(s) covering "
              f"{cluster_report.articles_clustered} article(s) — {cluster_report.by_method}")
    return 0


def cmd_import_feed(args) -> int:
    conn = _open(args)
    from .ingest import feeds
    with transaction(conn):
        report = feeds.import_feed(
            conn, campaign_ref=args.campaign, url=args.url, outlet_name=args.outlet,
            default_tier=args.default_tier or "newsletter",
        )
    print(report.summary())
    return 0


def cmd_import_gdelt(args) -> int:
    conn = _open(args)
    from .ingest import gdelt
    with transaction(conn):
        report = gdelt.import_query(
            conn, campaign_ref=args.campaign, query=args.query,
            start=args.start, end=args.end, max_records=args.max_records,
        )
    print(report.summary())
    print("GDELT supplies no article body, so these rows cannot be scanned for entity mentions.")
    return 0


def cmd_import_mediacloud(args) -> int:
    conn = _open(args)
    from .ingest import mediacloud
    with transaction(conn):
        report = mediacloud.import_query(
            conn, campaign_ref=args.campaign, query=args.query,
            start_date=args.start, end_date=args.end,
            collection=args.collection, limit=args.limit,
        )
    print(report.summary())
    return 0


def cmd_ingest(args) -> int:
    conn = _open(args)
    from .ingest import scheduled

    with transaction(conn):
        report = scheduled.run(
            conn, lookback_days=args.lookback_days, rule_ids=args.rule, dry_run=args.dry_run
        )
        clustered = {} if args.dry_run else scheduled.cluster_and_reindex(conn, report)

    print(report.summary())
    for result in report.results:
        state = f"ERROR {result.error}" if result.error else (
            f"{result.inserted} new / {result.duplicates} already present "
            f"/ {result.rejected} rejected of {result.rows_seen}"
        )
        print(f"  [{result.source}] {result.rule_name} -> {result.campaign_slug}: {state}")
        if result.hit_record_cap:
            print(f"      hit the {scheduled.GDELT_MAX_RECORDS}-record API cap for "
                  f"{result.window_start}..{result.window_end}. Coverage was dropped — run more "
                  "often, or narrow the query.")
    for note in report.skipped:
        print(f"  skipped: {note}")
    if clustered:
        print(f"  re-clustered: {clustered}")
    if not report.results and not report.skipped:
        print("  No enabled rule names an automated source. Add one with:\n"
              "    cib watch add --type gdelt_query --pattern '\"your query\"' --campaign <slug> "
              "--name 'GDELT'")
    return 0


def cmd_import_list(args) -> int:
    conn = _open(args)
    rows = import_repo.list_all(conn, args.limit)
    if args.json:
        _print_json([dict(r) for r in rows])
        return 0
    if not rows:
        print("No imports recorded.")
        return 0
    print(f"{'id':>4}  {'source':<11} {'imported':<21} {'rows':>6} {'new':>6} {'dup':>6} "
          f"{'rej':>5}  file")
    for r in rows:
        print(f"{r['id']:>4}  {r['source']:<11} {r['imported_at']:<21} {r['row_count']:>6} "
              f"{r['rows_inserted']:>6} {r['rows_skipped_duplicate']:>6} {r['rows_rejected']:>5}  "
              f"{r['file_name'] or '—'}")
    return 0


# --------------------------------------------------------------------------- dedup / entities

def cmd_cluster(args) -> int:
    conn = _open(args)
    campaign = campaign_repo.resolve(conn, args.campaign)
    with transaction(conn):
        report = dedup.cluster_campaign(conn, campaign.id, reset=not args.no_reset)
    _print_json({
        "campaign": campaign.slug,
        "clusters_created": report.clusters_created,
        "articles_clustered": report.articles_clustered,
        "by_method": report.by_method,
    })
    return 0


def cmd_entity_add(args) -> int:
    conn = _open(args)
    with transaction(conn):
        entity_id = entity_repo.create(
            conn, name=args.name, type=args.type, aliases=args.alias or [], notes=args.notes
        )
    print(f"Created entity #{entity_id} ({args.name}, {args.type})")
    return 0


def cmd_entity_list(args) -> int:
    conn = _open(args)
    rows = entity_repo.list_all(conn, args.type)
    if args.json:
        _print_json([{"id": e.id, "name": e.name, "type": e.type, "aliases": e.alias_list}
                     for e in rows])
        return 0
    for e in rows:
        print(f"{e.id:>3}  {e.type:<14} {e.name}"
              + (f"  (aliases: {', '.join(e.alias_list)})" if e.alias_list else ""))
    return 0


def cmd_entity_match(args) -> int:
    conn = _open(args)
    campaign = campaign_repo.resolve(conn, args.campaign)
    with transaction(conn):
        report = entity_matcher.match_campaign(
            conn, campaign.id, reset=not args.no_reset, use_llm=args.llm or None
        )
    _print_json({
        "campaign": campaign.slug,
        "articles_scanned": report.articles_scanned,
        "articles_without_body": report.articles_without_body,
        "mentions_created": report.mentions_created,
        "by_role": report.by_role,
        "by_entity": report.by_entity,
        "ambiguous_low_confidence": len(report.ambiguous),
    })
    if report.articles_without_body:
        print(f"\n{report.articles_without_body} article(s) have no body text and could not be "
              "scanned. Their mention count is unknown, not zero.")
    return 0


# --------------------------------------------------------------------------- events

def cmd_escalation_add(args) -> int:
    conn = _open(args)
    campaign = campaign_repo.resolve(conn, args.campaign)
    with transaction(conn):
        escalation_id = event_repo.add_escalation(
            conn, campaign_id=campaign.id, occurred_at=args.occurred_at,
            escalation_type=args.type, actor_name=args.actor, actor_type=args.actor_type,
            description=args.description, source_url=args.source_url, severity=args.severity,
        )
    print(f"Logged escalation #{escalation_id} ({args.type}, severity {args.severity} — "
          f"{SEVERITY_ANCHORS[args.severity]})")
    return 0


def cmd_escalation_verify(args) -> int:
    conn = _open(args)
    with transaction(conn):
        event_repo.verify_escalation(conn, args.id, args.by)
    print(f"Escalation #{args.id} marked verified.")
    return 0


def cmd_escalation_list(args) -> int:
    conn = _open(args)
    campaign = campaign_repo.resolve(conn, args.campaign)
    rows = event_repo.escalations_for(conn, campaign.id)
    if args.json:
        _print_json([dict(r) for r in rows])
        return 0
    if not rows:
        print("No escalations logged for this campaign. This log is maintained by hand: an empty "
              "log means nothing has been recorded, not that nothing has happened.")
        return 0
    for r in rows:
        mark = "✓" if r["verified_by"] else " "
        print(f"{r['id']:>4} {mark} {r['occurred_at'][:10]}  sev {r['severity']}  "
              f"{r['escalation_type']:<24} {r['actor_name']}\n       {r['source_url']}")
    return 0


def cmd_signal_add(args) -> int:
    conn = _open(args)
    campaign_id = campaign_repo.resolve(conn, args.campaign).id if args.campaign else None
    with transaction(conn):
        signal_id = event_repo.add_inbound_signal(
            conn, campaign_id=campaign_id, occurred_at=args.occurred_at,
            channel=args.channel, summary=args.summary, source_ref=args.source_ref,
        )
    print(f"Logged inbound signal #{signal_id} ({args.channel})")
    return 0


def cmd_outlet_reach(args) -> int:
    conn = _open(args)
    with transaction(conn):
        outlet_repo.set_reach(
            conn, args.outlet_id, reach_value=args.value, reach_source=args.source,
            is_estimated=args.estimated, estimation_method=args.method, as_of=args.as_of,
        )
    outlet = outlet_repo.get(conn, args.outlet_id)
    print(f"{outlet.name}: reach {outlet.reach_value:,} "
          f"({'estimated — ' + (outlet.reach_estimation_method or '') if outlet.reach_is_estimated else 'reported'}), "
          f"source: {outlet.reach_source}")
    return 0


def cmd_outlet_list(args) -> int:
    conn = _open(args)
    rows = outlet_repo.list_all(conn)
    if args.json:
        _print_json([r.__dict__ for r in rows])
        return 0
    with_reach = sum(1 for o in rows if o.reach_value is not None)
    for o in rows:
        reach = f"{o.reach_value:,}" if o.reach_value is not None else "no sourced reach"
        print(f"{o.id:>4}  {o.tier:<18} {o.name[:38]:<38} {o.country or '--':<3} {reach}")
    print(f"\n{with_reach} of {len(rows)} outlets have a sourced reach value.")
    return 0


# --------------------------------------------------------------------------- metrics

def cmd_metrics(args) -> int:
    conn = _open(args)
    try:
        result = campaign_metrics(conn, args.campaign, args.at_day)
    except PrePublicationError as exc:
        print(f"{exc}\n")
        _print_json(prepublication_view(conn, args.campaign))
        return 0
    if args.json:
        _print_json(result.to_dict())
        return 0

    print(f"{result.campaign_name}  ({result.campaign_slug})")
    print(f"Cutoff: {'day ' + str(result.at_day_index) if result.at_day_index is not None else 'no cutoff — lifetime totals'}\n")
    for key in METRIC_ORDER:
        metric = result.get(key)
        if metric is None:
            continue
        if not metric.available:
            print(f"  {metric.label:<34} not available — {metric.unavailable_reason}")
            continue
        value = metric.value
        shown = json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value
        print(f"  {metric.label:<34} {shown}   [{len(metric.row_ids)} source rows]")
    if result.caveats:
        print("\nCaveats:")
        for caveat in result.caveats:
            print(f"  - {caveat}")
    print("\nData quality:")
    for key, value in result.data_quality.items():
        if key != "sources":
            print(f"  {key}: {value}")
    return 0


def cmd_compare(args) -> int:
    conn = _open(args)
    result = compare(conn, args.campaigns, args.at_day)
    if args.json:
        _print_json(result)
        return 0

    columns = result["campaigns"]
    if columns:
        slugs = [c["campaign_slug"][:20] for c in columns]
        print(f"Comparison at day {result['at_day_index']} "
              f"({'explicit cutoff' if result['cutoff_was_explicit'] else 'derived cutoff'})\n")
        header = f"  {'Metric':<32}" + "".join(f"{s:>22}" for s in slugs)
        print(header)
        print("  " + "-" * (len(header) - 2))
        for key in METRIC_ORDER:
            cells, label = [], None
            for column in columns:
                metric = column["metrics"].get(key)
                if metric is None:
                    cells.append("—")
                    continue
                label = metric["label"]
                if not metric["available"]:
                    cells.append("n/a")
                elif isinstance(metric["value"], dict):
                    cells.append(f"({len(metric['value'])} groups)")
                else:
                    cells.append(str(metric["value"]))
            if label:
                # Truncate rather than let a long label push the value columns out of alignment.
                shown = label if len(label) <= 32 else f"{label[:31]}…"
                print(f"  {shown:<32}" + "".join(f"{c[:21]:>22}" for c in cells))

    for view in result["pre_publication"]:
        print(f"\n{view['campaign']['slug']}: {view['footprint_refusal']}")
        print(f"  Publisher precedents recorded: {view['precedent_count']}")
        print(f"  Inbound signals logged: {view['inbound_signal_count']}")
        print(f"  Watch rules enabled: {view['watch_rules_enabled']} "
              f"of {len(view['watch_rules'])}")

    if result["caveats"]:
        print("\nCaveats — these must travel with the figures above:")
        for caveat in result["caveats"]:
            print(f"  - {caveat}")
    return 0


def cmd_definitions(args) -> int:
    if args.json:
        _print_json({k: d.__dict__ for k, d in metric_definitions.DEFINITIONS.items()})
        return 0
    for key in METRIC_ORDER:
        d = metric_definitions.DEFINITIONS.get(key)
        if d is None:
            continue
        print(f"{d.label}  ({d.key}, {d.unit})")
        print(f"  {d.plain}")
        print(f"  Counts: {d.counts}")
        if d.caveat:
            print(f"  Caveat: {d.caveat}")
        print()
    return 0


def cmd_snapshot(args) -> int:
    conn = _open(args)
    with transaction(conn):
        written = (snapshot_module.rebuild_all(conn) if args.all
                   else snapshot_module.rebuild_live(conn))
    _print_json(written)
    return 0


# --------------------------------------------------------------------------- watch

def cmd_watch_list(args) -> int:
    conn = _open(args)
    rows = query(conn, """
        SELECT r.*, c.slug AS campaign_slug,
               (SELECT COUNT(*) FROM watch_hits h WHERE h.rule_id = r.id) AS hits
          FROM watch_rules r LEFT JOIN campaigns c ON c.id = r.campaign_id ORDER BY r.id
    """)
    if args.json:
        _print_json([dict(r) for r in rows])
        return 0
    if not rows:
        print("No watch rules.")
        return 0
    for r in rows:
        state = "enabled " if r["enabled"] else "DISABLED"
        promo = " promotes-to-live" if r["promotes_to_live"] else ""
        print(f"{r['id']:>3}  {state}  {r['rule_type']:<12} {r['name'][:42]:<42} "
              f"hits={r['hits']:<4} last_polled={r['last_polled_at'] or 'never'}{promo}")
        print(f"      pattern: {r['pattern']}")
        if r["last_error"]:
            print(f"      last error: {r['last_error']}")
    return 0


def cmd_watch_add(args) -> int:
    conn = _open(args)
    from .watch import rules as rule_repo
    campaign_id = campaign_repo.resolve(conn, args.campaign).id if args.campaign else None
    with transaction(conn):
        rule_id = rule_repo.create(
            conn, name=args.name, rule_type=args.type, pattern=args.pattern,
            campaign_id=campaign_id, source_url=args.source_url,
            enabled=not args.disabled, promotes_to_live=args.promotes_to_live, notes=args.notes,
        )
    print(f"Created watch rule #{rule_id}"
          + (" (promotes campaign to live on hit)" if args.promotes_to_live else ""))
    return 0


def cmd_watch_enable(args) -> int:
    conn = _open(args)
    from .watch import rules as rule_repo
    with transaction(conn):
        rule_repo.set_enabled(conn, args.id, not args.off)
    print(f"Rule #{args.id} {'disabled' if args.off else 'enabled'}.")
    return 0


def cmd_watch_poll(args) -> int:
    conn = _open(args)
    from .watch import poller
    with transaction(conn):
        report = poller.poll_once(conn, args.rule, notify=not args.no_notify)
    print(report.summary())
    for promotion in report.promotions:
        print(f"\nPROMOTED TO LIVE: {promotion['campaign_slug']} "
              f"published_at set to {promotion['published_at']} by rule "
              f"'{promotion['triggered_by_rule']}' ({promotion['matched_url']})")
    if args.json:
        _print_json(report.detail)
    return 0


def cmd_watch_test_alert(args) -> int:
    """Prove the alert path works before relying on it to catch day zero."""
    from .watch.notify import Alert, describe_channels, send

    described = describe_channels(args.channels)
    if not described:
        print("No channels configured. Set CIB_NOTIFY_CHANNELS in .env.")
        return 1

    print("Configured channels:")
    for name, usable, note in described:
        print(f"  {'OK  ' if usable else 'FAIL'} {name:<9} {note}")

    unusable = [n for n, usable, _ in described if not usable]
    if unusable and not args.force:
        print(f"\nNot sending: {', '.join(unusable)} cannot deliver. Fix the settings above, "
              "or pass --force to try anyway.")
        return 1

    print("\nSending a test alert…")
    outcome = send(Alert(
        title="Campaign Impact Benchmarker — test alert",
        body="If you are reading this, watch-hit alerts will reach you. "
             "This message was sent by `cib watch test-alert` and is not a real hit.",
        rule_name="test-alert",
    ), args.channels)
    for name, result in outcome.items():
        print(f"  {name}: {result}")
    return 0 if all(r == "sent" for r in outcome.values()) else 1


def cmd_doctor(args) -> int:
    conn = _open(args)
    from . import doctor

    report = doctor.run(conn)
    if args.json:
        _print_json(report.to_dict())
        return 1 if (args.strict and report.blockers) else 0

    labels = {"blocker": "BLOCKER", "warning": "WARNING", "ok": "OK"}
    for level in ("blocker", "warning", "ok"):
        findings = report.of(level)
        if not findings:
            continue
        heading = {
            "blocker": "cannot produce a measurement until these are fixed",
            "warning": "figures will be incomplete, or something is unmonitored",
            "ok": "checked and healthy",
        }[level]
        print(f"\n{labels[level]} ({len(findings)}) — {heading}")
        print("-" * 78)
        for f in findings:
            print(f"  {f.title}")
            if f.detail:
                for line in _wrap(f.detail, 74):
                    print(f"      {line}")
            if f.fix:
                print(f"      fix: {f.fix}")
            print()

    print(f"{len(report.blockers)} blocker(s), {len(report.warnings)} warning(s), "
          f"{len(report.of('ok'))} ok.")
    if report.blockers:
        print("\nThe blockers above are the difference between a tool that runs and a tool that "
              "measures something.")
    return 1 if (args.strict and report.blockers) else 0


def _wrap(text: str, width: int) -> list[str]:
    import textwrap
    return textwrap.wrap(text, width) or [""]


def cmd_watch_hits(args) -> int:
    conn = _open(args)
    from .watch import rules as rule_repo
    campaign_id = campaign_repo.resolve(conn, args.campaign).id if args.campaign else None
    rows = rule_repo.recent_hits(conn, args.limit, campaign_id)
    if args.json:
        _print_json([dict(r) for r in rows])
        return 0
    for r in rows:
        print(f"{r['detected_at']}  [{r['rule_name']}] {r['matched_title'] or ''}")
        print(f"    {r['matched_url']}")
    if not rows:
        print("No watch hits recorded.")
    return 0


def cmd_watch_run(args) -> int:
    from .watch import poller
    poller.run_scheduler(args.db, args.poll_minutes, args.snapshot_hour)
    return 0


# --------------------------------------------------------------------------- export

def cmd_export(args) -> int:
    conn = _open(args)
    from .export import briefing, tables

    if args.what != "snapshot" and not args.campaigns:
        raise ValueError(f"`cib export {args.what}` needs at least one campaign.")

    if args.what == "snapshot":
        from .export import snapshot_json
        out = Path(args.out or snapshot_json.DEFAULT_OUT)
        if not out.is_absolute():
            out = config.REPO_ROOT / out
        assets = config.REPO_ROOT / snapshot_json.DEFAULT_ASSETS_DIR
        payload = snapshot_json.write(conn, out, assets_dir=assets,
                                      extra_warnings=args.warn)
        stats = payload["stats"]
        print(f"Wrote {out} ({out.stat().st_size / 1024:.1f} KB)")
        print(f"  {len(payload.get('static_assets', []))} export file(s) -> {assets}")
        print(f"  campaigns={stats['campaigns']} articles={stats['articles']} "
              f"comparisons baked={stats['comparisons_baked']}")
        for warning in payload["warnings"]:
            print(f"  warning: {warning}")
        return 0

    if args.what == "comparison":
        output = tables.comparison_csv(conn, args.campaigns, args.at_day)
    elif args.what == "articles":
        output = tables.articles_csv(conn, campaign_repo.resolve(conn, args.campaigns[0]).id)
    elif args.what == "escalations":
        output = tables.escalations_csv(conn, campaign_repo.resolve(conn, args.campaigns[0]).id)
    elif args.what == "mentions":
        output = tables.mentions_csv(conn, campaign_repo.resolve(conn, args.campaigns[0]).id)
    else:  # briefing
        data = briefing.build(conn, args.campaigns, args.at_day)
        output = briefing.to_html(data) if args.format == "html" else briefing.to_markdown(data)

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        sys.stdout.write(output)
    return 0


def cmd_serve(args) -> int:
    try:
        import uvicorn
    except ImportError:
        print("The API needs the api extra: pip install -e '.[api]'", file=sys.stderr)
        return 1
    uvicorn.run("cib.api.main:app", host=args.host, port=args.port, reload=args.reload)
    return 0


# --------------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cib",
        description="Campaign Impact Benchmarker — measure and compare campaign media footprints.",
    )
    parser.add_argument("--db", help="SQLite path (default: CIB_DB_PATH or data/cib.sqlite3)")
    parser.add_argument("--as", dest="as_actor",
                        help="Identity recorded in created_by on every write")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Create or migrate the database").set_defaults(func=cmd_init)

    p = sub.add_parser("seed", help="Create the three placeholder campaign records")
    p.add_argument("--force", action="store_true", help="Recreate campaigns that already exist")
    p.set_defaults(func=cmd_seed)

    # campaign
    campaign = sub.add_parser("campaign", help="Campaign records").add_subparsers(
        dest="subcommand", required=True)
    campaign.add_parser("list", help="List campaigns").set_defaults(func=cmd_campaign_list)

    p = campaign.add_parser("add", help="Create a campaign")
    p.add_argument("--name", required=True)
    p.add_argument("--publisher", required=True)
    p.add_argument("--type", required=True,
                   choices=["journalism", "ngo_report", "coalition", "regulatory"])
    p.add_argument("--status", required=True,
                   choices=["pre_publication", "live", "decaying", "archived"])
    p.add_argument("--published-at", dest="published_at",
                   help="ISO date/time. Omit for a pre_publication campaign.")
    p.add_argument("--first-signal-at", dest="first_signal_at")
    p.add_argument("--timezone", default="Europe/Copenhagen")
    p.add_argument("--theme", action="append")
    p.add_argument("--slug")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_campaign_add)

    p = campaign.add_parser("set-published", help="Set a campaign's day zero and reindex")
    p.add_argument("campaign")
    p.add_argument("published_at")
    p.add_argument("--status", default="live",
                   choices=["live", "decaying", "archived"])
    p.set_defaults(func=cmd_campaign_set_published)

    p = campaign.add_parser("precedent", help="Link a past campaign as publisher precedent")
    p.add_argument("campaign")
    p.add_argument("precedent")
    p.add_argument("--rationale", required=True)
    p.set_defaults(func=cmd_campaign_precedent)

    # import
    imp = sub.add_parser("import", help="Ingest coverage").add_subparsers(
        dest="subcommand", required=True)

    p = imp.add_parser("inspect", help="Show headers and the guessed column mapping")
    p.add_argument("--file", required=True)
    p.add_argument("--source", default="csv",
                   choices=["csv", "infomedia", "factiva", "nexis"])
    p.add_argument("--encoding", default="utf-8-sig")
    p.set_defaults(func=cmd_import_inspect)

    for source in ("csv", "infomedia", "factiva", "nexis"):
        p = imp.add_parser(source, help=f"Import a {source} export")
        p.add_argument("--campaign", required=True)
        p.add_argument("--file", required=True)
        p.add_argument("--map", action="append", metavar="FIELD=COLUMN")
        p.add_argument("--encoding", default="utf-8-sig" if source != "factiva" else "utf-8")
        p.add_argument("--default-tier")
        p.add_argument("--default-country")
        p.add_argument("--default-language")
        p.add_argument("--notes")
        p.add_argument("--no-cluster", action="store_true",
                       help="Skip the syndication clustering pass")
        p.set_defaults(func=cmd_import_file,
                       source="manual" if source == "csv" else source)

    p = imp.add_parser("feed", help="Import an RSS, Atom or news sitemap feed")
    p.add_argument("--campaign", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--outlet")
    p.add_argument("--default-tier")
    p.set_defaults(func=cmd_import_feed)

    p = imp.add_parser("gdelt", help="Import from the GDELT DOC 2.0 API")
    p.add_argument("--campaign", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--start", help="YYYYMMDDHHMMSS")
    p.add_argument("--end", help="YYYYMMDDHHMMSS")
    p.add_argument("--max-records", type=int, default=250)
    p.set_defaults(func=cmd_import_gdelt)

    p = imp.add_parser("mediacloud", help="Import from the Media Cloud API")
    p.add_argument("--campaign", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--collection")
    p.add_argument("--limit", type=int, default=1000)
    p.set_defaults(func=cmd_import_mediacloud)

    p = sub.add_parser(
        "ingest",
        help="Import from every enabled watch rule that names an automated source (GDELT, "
             "opted-in feeds). This is what the scheduled workflow runs.",
    )
    p.add_argument("--lookback-days", type=int, default=7,
                   help="How far back to fetch when a rule has never run (default: 7)")
    p.add_argument("--rule", type=int, action="append", help="Only these rule ids")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would be fetched without calling any API")
    p.set_defaults(func=cmd_ingest)

    p = imp.add_parser("list", help="Show the import ledger")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_import_list)

    # cluster
    p = sub.add_parser("cluster", help="Re-run syndication clustering")
    p.add_argument("campaign")
    p.add_argument("--no-reset", action="store_true",
                   help="Keep existing machine clusters instead of rebuilding")
    p.set_defaults(func=cmd_cluster)

    # entity
    entity = sub.add_parser("entity", help="Entities and mentions").add_subparsers(
        dest="subcommand", required=True)
    p = entity.add_parser("add", help="Create an entity")
    p.add_argument("--name", required=True)
    p.add_argument("--type", required=True,
                   choices=["own_company", "competitor", "ngo", "certifier",
                            "customer", "supplier", "regulator"])
    p.add_argument("--alias", action="append")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_entity_add)

    p = entity.add_parser("list", help="List entities")
    p.add_argument("--type")
    p.set_defaults(func=cmd_entity_list)

    p = entity.add_parser("match", help="Detect mentions across a campaign's articles")
    p.add_argument("campaign")
    p.add_argument("--no-reset", action="store_true")
    p.add_argument("--llm", action="store_true",
                   help="Use the LLM fallback for ambiguous roles (needs ANTHROPIC_API_KEY)")
    p.set_defaults(func=cmd_entity_match)

    # escalation
    esc = sub.add_parser("escalation", help="Escalation log").add_subparsers(
        dest="subcommand", required=True)
    p = esc.add_parser("add", help="Log an escalation")
    p.add_argument("--campaign", required=True)
    p.add_argument("--occurred-at", dest="occurred_at", required=True)
    p.add_argument("--type", required=True,
                   choices=["legal_petition", "regulatory_action", "customs_measure",
                            "retailer_statement", "buyer_statement", "parliamentary_question",
                            "certifier_response", "company_response", "other"])
    p.add_argument("--actor", required=True)
    p.add_argument("--actor-type", dest="actor_type")
    p.add_argument("--description", required=True)
    p.add_argument("--source-url", dest="source_url", required=True,
                   help="Required: an escalation is a claim about the outside world")
    p.add_argument("--severity", type=int, required=True, choices=[1, 2, 3, 4, 5],
                   help="; ".join(f"{k}={v}" for k, v in SEVERITY_ANCHORS.items()))
    p.set_defaults(func=cmd_escalation_add)

    p = esc.add_parser("verify", help="Mark an escalation verified")
    p.add_argument("id", type=int)
    p.add_argument("--by")
    p.set_defaults(func=cmd_escalation_verify)

    p = esc.add_parser("list", help="List a campaign's escalations")
    p.add_argument("campaign")
    p.set_defaults(func=cmd_escalation_list)

    # signal
    p = sub.add_parser("signal", help="Log an inbound signal")
    p.add_argument("--campaign")
    p.add_argument("--occurred-at", dest="occurred_at", required=True)
    p.add_argument("--channel", required=True,
                   choices=["journalist", "customer", "tender", "investor", "employee", "other"])
    p.add_argument("--summary", required=True)
    p.add_argument("--source-ref", dest="source_ref")
    p.set_defaults(func=cmd_signal_add)

    # outlet
    outlet = sub.add_parser("outlet", help="Outlets and reach").add_subparsers(
        dest="subcommand", required=True)
    outlet.add_parser("list", help="List outlets").set_defaults(func=cmd_outlet_list)
    p = outlet.add_parser("reach", help="Record an outlet's audience figure")
    p.add_argument("outlet_id", type=int)
    p.add_argument("--value", type=int, required=True)
    p.add_argument("--source", required=True,
                   help="Required: reach figures must name where they came from")
    p.add_argument("--estimated", action="store_true")
    p.add_argument("--method", help="Required when --estimated is set")
    p.add_argument("--as-of", dest="as_of")
    p.set_defaults(func=cmd_outlet_reach)

    # metrics / compare
    p = sub.add_parser("metrics", help="All metrics for one campaign")
    p.add_argument("campaign")
    p.add_argument("--at-day", dest="at_day", type=int,
                   help="Truncate at this day index (inclusive)")
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser("compare", help="Compare 2-4 campaigns at the same day index")
    p.add_argument("campaigns", nargs="+")
    p.add_argument("--at-day", dest="at_day", type=int,
                   help="Day-index cutoff. Omitted: the shortest observed window is used.")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser(
        "doctor",
        help="Report what is still a placeholder or misconfigured, and how to fix each",
    )
    p.add_argument("--strict", action="store_true",
                   help="Exit non-zero if any blocker is found (for CI)")
    p.set_defaults(func=cmd_doctor)

    sub.add_parser("definitions", help="Print every metric's plain-language definition"
                   ).set_defaults(func=cmd_definitions)

    p = sub.add_parser("snapshot", help="Write metrics_daily rows")
    p.add_argument("--all", action="store_true",
                   help="Include archived campaigns, not only live and decaying ones")
    p.set_defaults(func=cmd_snapshot)

    # watch
    watch = sub.add_parser("watch", help="Trigger and watch system").add_subparsers(
        dest="subcommand", required=True)
    watch.add_parser("list", help="List watch rules").set_defaults(func=cmd_watch_list)

    p = watch.add_parser("add", help="Create a watch rule")
    p.add_argument("--name", required=True)
    p.add_argument("--type", required=True,
                   choices=["keyword", "phrase", "byline", "domain", "rss", "sitemap",
                            "gdelt_query"])
    p.add_argument("--pattern", required=True)
    p.add_argument("--campaign")
    p.add_argument("--source-url", dest="source_url")
    p.add_argument("--disabled", action="store_true")
    p.add_argument("--promotes-to-live", dest="promotes_to_live", action="store_true",
                   help="On a hit, set the campaign's published_at and start snapshotting")
    p.add_argument("--notes")
    p.set_defaults(func=cmd_watch_add)

    p = watch.add_parser("enable", help="Enable or disable a rule")
    p.add_argument("id", type=int)
    p.add_argument("--off", action="store_true")
    p.set_defaults(func=cmd_watch_enable)

    p = watch.add_parser("poll", help="Poll every enabled rule once")
    p.add_argument("--rule", type=int, action="append", help="Poll only these rule ids")
    p.add_argument("--no-notify", action="store_true")
    p.set_defaults(func=cmd_watch_poll)

    p = watch.add_parser("test-alert",
                         help="Send a test alert, to prove the channel works before you need it")
    p.add_argument("--channels", help="Override CIB_NOTIFY_CHANNELS for this test")
    p.add_argument("--force", action="store_true",
                   help="Send even if a channel looks unconfigured")
    p.set_defaults(func=cmd_watch_test_alert)

    p = watch.add_parser("hits", help="Recent watch hits")
    p.add_argument("--campaign")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_watch_hits)

    p = watch.add_parser("run", help="Run the poller and daily snapshot on a schedule")
    p.add_argument("--poll-minutes", type=int, default=30)
    p.add_argument("--snapshot-hour", type=int, default=3)
    p.set_defaults(func=cmd_watch_run)

    # export
    p = sub.add_parser("export", help="CSV tables and the one-page briefing")
    p.add_argument("what", choices=["comparison", "articles", "escalations", "mentions",
                                    "briefing", "snapshot"])
    p.add_argument("campaigns", nargs="*",
                   help="Campaign slugs or ids. Not used by `snapshot`, which covers all of them.")
    p.add_argument("--at-day", dest="at_day", type=int)
    p.add_argument("--format", default="markdown", choices=["markdown", "html"],
                   help="Briefing only")
    p.add_argument("--out", help="Write to this path instead of stdout "
                                 "(snapshot: default web/src/lib/seed.json)")
    p.add_argument("--warn", action="append",
                   help="snapshot only: extra warning shown on every page of the static build. "
                        "Use it to label a snapshot built from demo data.")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("serve", help="Run the HTTP API the dashboard talks to")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    set_actor(getattr(args, "as_actor", None))
    if not hasattr(args, "json"):
        args.json = False
    try:
        return args.func(args)
    except (LookupError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
