"""One-page briefing export.

Written to be handed to a crisis or leadership team and defended line by line. That constraint
drives every choice here: each figure appears with its plain-language definition, its caveats, and
the count of source rows behind it, and the evidence appendix lists those rows with links and
import provenance.

Renders as Markdown (plain text, mailable) or as a self-contained HTML page for printing.
"""

from __future__ import annotations

import html
import sqlite3
from typing import Any

from ..actor import actor, now_utc
from ..db import query

# Imported from the submodule, not the package: cib.metrics re-exports compare() as a
# function, which shadows the module of the same name.
from ..metrics.comparison import METRIC_ORDER, campaign_metrics, compare, prepublication_view
from ..models import SEVERITY_ANCHORS
from ..repo import campaigns as campaign_repo

EVIDENCE_LIMIT = 60


def _format_value(metric: dict) -> str:
    if not metric["available"]:
        return f"not available — {metric['unavailable_reason']}"
    value = metric["value"]
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict):
        return ", ".join(f"{k}: {v['articles'] if isinstance(v, dict) else v}"
                         for k, v in list(value.items())[:6]) or "—"
    return str(value)


def build(conn: sqlite3.Connection, campaign_refs: list[str | int],
          at_day_index: int | None = None,
          include_evidence: bool = True) -> dict[str, Any]:
    """Assemble the briefing's content. Rendering is separate so both formats share one source."""
    primary = campaign_repo.resolve(conn, campaign_refs[0])
    comparison = (compare(conn, campaign_refs, at_day_index)
                  if len(campaign_refs) > 1 else None)

    if primary.published_at is None:
        detail: dict[str, Any] = {"mode": "pre_publication",
                                  "view": prepublication_view(conn, primary.id)}
    else:
        metric_set = campaign_metrics(conn, primary.id, at_day_index)
        detail = {"mode": "published", "metrics": metric_set.to_dict()}

    escalations = [dict(r) for r in query(conn, """
        SELECT * FROM escalations WHERE campaign_id = ? ORDER BY occurred_at
    """, (primary.id,))]

    evidence = []
    if include_evidence and primary.published_at is not None:
        evidence = [dict(r) for r in query(conn, """
            SELECT a.id, a.day_index, a.published_at, a.headline, a.url, a.is_original,
                   o.name AS outlet, o.tier,
                   i.source AS import_source, i.file_name, i.imported_at
              FROM articles a
              JOIN outlets o ON o.id = a.outlet_id
              JOIN imports i ON i.id = a.import_id
             WHERE a.campaign_id = ?
             ORDER BY a.day_index, a.published_at
             LIMIT ?
        """, (primary.id, EVIDENCE_LIMIT))]

    total_articles = query(conn, "SELECT COUNT(*) AS n FROM articles WHERE campaign_id = ?",
                           (primary.id,))[0]["n"]

    return {
        "generated_at": now_utc(),
        "generated_by": actor(),
        "campaign": {
            "id": primary.id, "slug": primary.slug, "name": primary.name,
            "publisher_org": primary.publisher_org, "campaign_type": primary.campaign_type,
            "status": primary.status, "published_at": primary.published_at,
            "first_signal_at": primary.first_signal_at, "themes": primary.theme_list,
            "notes": primary.notes,
        },
        "detail": detail,
        "comparison": comparison,
        "escalations": escalations,
        "evidence": evidence,
        "evidence_total": int(total_articles),
        "evidence_shown": len(evidence),
        "severity_anchors": SEVERITY_ANCHORS,
    }


def to_markdown(data: dict[str, Any]) -> str:
    c = data["campaign"]
    lines: list[str] = [
        f"# Campaign briefing: {c['name']}",
        "",
        f"*Generated {data['generated_at']} by {data['generated_by']}. "
        "Every figure below is measured from imported records and drills down to the source rows "
        "listed in the evidence appendix.*",
        "",
        "## 1. Campaign",
        "",
        f"- **Publisher:** {c['publisher_org']}",
        f"- **Type:** {c['campaign_type']}",
        f"- **Status:** {c['status']}",
        f"- **Published:** {c['published_at'] or 'not yet published'}",
        f"- **First internal signal:** {c['first_signal_at'] or 'not recorded'}",
        f"- **Themes:** {', '.join(c['themes']) or 'none recorded'}",
    ]
    if c["notes"]:
        lines += ["", c["notes"]]

    if data["detail"]["mode"] == "pre_publication":
        view = data["detail"]["view"]
        lines += [
            "", "## 2. This campaign has not published", "",
            f"> {view['footprint_refusal']}", "",
            f"### Publisher precedent ({view['precedent_count']} recorded)", "",
        ]
        if not view["publisher_precedent"]:
            lines.append("_No precedent campaigns have been linked to this one yet._")
        for p in view["publisher_precedent"]:
            lines += [
                f"**{p['name']}** — {p['publisher_org']}, published {p['published_at'] or 'n/a'}",
                f"  - Linked as precedent by {p['asserted_by']}: {p['rationale']}",
            ]
            for metric in (p.get("footprint") or {}).values():
                lines.append(
                    f"  - {metric['label']}: {_format_value(metric)} "
                    f"({metric['row_count']} source rows)"
                )
            lines.append("")
        lines += [f"### Logged internal signals ({view['inbound_signal_count']})", ""]
        if not view["inbound_signals"]:
            lines.append("_No inbound signals logged._")
        for s in view["inbound_signals"]:
            lines.append(
                f"- **{s['occurred_at'][:10]}** ({s['channel']}) {s['summary']} "
                f"— logged by {s['logged_by']}"
            )
        lines += ["", f"### Watch rules ({view['watch_rules_enabled']} enabled)", ""]
        for w in view["watch_rules"]:
            state = "enabled" if w["enabled"] else "disabled"
            promo = ", promotes to live" if w["promotes_to_live"] else ""
            lines.append(
                f"- `{w['rule_type']}` **{w['name']}** — {state}{promo}; "
                f"{w['hits']} hit(s); last polled {w['last_polled_at'] or 'never'}"
            )
    else:
        metrics = data["detail"]["metrics"]
        lines += [
            "", f"## 2. Measured footprint (cutoff: day {metrics['at_day_index']})"
            if metrics["at_day_index"] is not None else "", "",
            "| Metric | Value | Source rows | What it counts |",
            "|---|---|---|---|",
        ]
        for key in METRIC_ORDER:
            metric = metrics["metrics"].get(key)
            if metric is None:
                continue
            lines.append(
                f"| {metric['label']} | {_format_value(metric)} | {metric['row_count']} "
                f"| {metric['definition']['counts']} |"
            )
        if metrics["caveats"]:
            lines += ["", "### Caveats that must travel with these figures", ""]
            lines += [f"- {caveat}" for caveat in metrics["caveats"]]
        q = metrics["data_quality"]
        lines += [
            "", "### Data quality", "",
            f"- Articles in window: {q['articles_in_window']} "
            f"(of {q['articles_total_imported']} imported)",
            f"- Articles that could not be dated: {q['articles_undated']}",
            f"- Articles with no country: {q['articles_missing_country_pct']}%",
            f"- Articles with body text: {q['articles_with_body_text_pct']}%",
            f"- Outlets with no sourced reach figure: {q['outlets_missing_reach_pct']}%",
            f"- Last import: {q['last_import_at'] or 'never'}",
            "- Sources: " + (", ".join(
                f"{s['source']} ({s['articles']} articles, last {s['last_imported_at']})"
                for s in q["sources"]
            ) or "none"),
        ]

    if data["comparison"]:
        comparison = data["comparison"]
        slugs = [col["campaign_slug"] for col in comparison["campaigns"]]
        lines += [
            "", f"## 3. Comparison at day {comparison['at_day_index']}", "",
            "*Every campaign below is truncated at the same day index, so a campaign at day 5 is "
            "compared against past campaigns at their day 5, not their lifetime totals.*", "",
            "| Metric | " + " | ".join(slugs) + " |",
            "|---" * (len(slugs) + 1) + "|",
        ]
        for key in METRIC_ORDER:
            cells = []
            label = None
            for col in comparison["campaigns"]:
                metric = col["metrics"].get(key)
                if metric is None:
                    cells.append("—")
                    continue
                label = metric["label"]
                cells.append(_format_value(metric))
            if label:
                lines.append(f"| {label} | " + " | ".join(cells) + " |")
        if comparison["caveats"]:
            lines += ["", "**Comparison caveats**", ""]
            lines += [f"- {caveat}" for caveat in comparison["caveats"]]

    lines += ["", "## 4. Escalation timeline", ""]
    if not data["escalations"]:
        lines.append(
            "_No escalations logged. This log is maintained by hand: an empty log means nothing "
            "has been recorded, not that nothing has happened._"
        )
    else:
        lines += ["| Date | Type | Actor | Severity | Source |", "|---|---|---|---|---|"]
        for e in data["escalations"]:
            anchor = data["severity_anchors"].get(e["severity"], "")
            verified = " ✓" if e["verified_by"] else ""
            lines.append(
                f"| {e['occurred_at'][:10]} | {e['escalation_type']} | {e['actor_name']}{verified} "
                f"| {e['severity']} — {anchor} | {e['source_url']} |"
            )
        lines += ["", "Severity scale: " + "; ".join(
            f"{k} = {v}" for k, v in data["severity_anchors"].items()
        )]

    lines += ["", "## 5. Evidence appendix", ""]
    if not data["evidence"]:
        lines.append("_No articles imported for this campaign._")
    else:
        lines.append(
            f"Showing {data['evidence_shown']} of {data['evidence_total']} article rows. "
            "Every figure above resolves to rows of this kind; the full set is available as CSV "
            "with `cib export articles`."
        )
        lines += ["", "| Day | Date | Outlet | Headline | Original | Imported from |",
                  "|---|---|---|---|---|---|"]
        for a in data["evidence"]:
            headline = (a["headline"] or "")[:90]
            link = f"[{headline}]({a['url']})" if a["url"] else headline
            lines.append(
                f"| {a['day_index'] if a['day_index'] is not None else '—'} "
                f"| {a['published_at'][:10]} | {a['outlet']} | {link} "
                f"| {'yes' if a['is_original'] else 'syndicated'} "
                f"| {a['import_source']} {a['file_name'] or ''} ({a['imported_at'][:10]}) |"
            )
    return "\n".join(lines) + "\n"


_CSS = """
:root { color-scheme: light; }
body { font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       max-width: 62rem; margin: 2rem auto; padding: 0 1.5rem; color: #16191d; background: #fff; }
h1 { font-size: 1.7rem; margin-bottom: .25rem; }
h2 { font-size: 1.15rem; margin-top: 2rem; border-bottom: 1px solid #d8dde3; padding-bottom: .3rem; }
h3 { font-size: 1rem; margin-top: 1.4rem; }
table { border-collapse: collapse; width: 100%; margin: .8rem 0; font-size: .87rem; }
th, td { border: 1px solid #d8dde3; padding: .4rem .55rem; text-align: left; vertical-align: top; }
th { background: #f2f4f7; font-weight: 600; }
blockquote { border-left: 3px solid #c0392b; margin: 1rem 0; padding: .5rem 1rem;
             background: #fdf3f2; }
code { background: #f2f4f7; padding: .1rem .3rem; border-radius: 3px; font-size: .85em; }
.meta { color: #5a6472; font-size: .85rem; }
.caveat { background: #fffbe6; border-left: 3px solid #d9a300; padding: .5rem 1rem; margin: .8rem 0; }
@media print { body { margin: 0; max-width: none; font-size: 11pt; } h2 { page-break-after: avoid; } }
"""


def to_html(data: dict[str, Any]) -> str:
    """Render the same briefing as a printable page. Markdown is converted inline, minimally."""
    md = to_markdown(data)
    body: list[str] = []
    in_table = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(set(c) <= {"-", ":", " "} and c for c in cells):
                continue
            tag = "th" if not in_table else "td"
            if not in_table:
                body.append("<table>")
                in_table = True
            body.append("<tr>" + "".join(
                f"<{tag}>{_inline(c)}</{tag}>" for c in cells
            ) + "</tr>")
            continue
        if in_table:
            body.append("</table>")
            in_table = False
        if line.startswith("### "):
            body.append(f"<h3>{html.escape(line[4:])}</h3>")
        elif line.startswith("## "):
            body.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("# "):
            body.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("> "):
            body.append(f"<blockquote>{_inline(line[2:])}</blockquote>")
        elif line.startswith("- "):
            body.append(f"<p style='margin:.2rem 0 .2rem 1rem'>&bull; {_inline(line[2:])}</p>")
        elif line.startswith("*") and line.endswith("*") and len(line) > 2:
            body.append(f"<p class='meta'>{_inline(line.strip('*'))}</p>")
        elif line:
            body.append(f"<p>{_inline(line)}</p>")
    if in_table:
        body.append("</table>")

    title = html.escape(data["campaign"]["name"])
    return (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>Briefing — {title}</title><style>{_CSS}</style></head><body>"
        + "\n".join(body)
        + "</body></html>"
    )


def _inline(text: str) -> str:
    """Escape, then restore the small subset of Markdown the briefing actually uses."""
    import re

    out = html.escape(text)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)",
                 lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"_([^_]+)_", r"<em>\1</em>", out)
    return out
