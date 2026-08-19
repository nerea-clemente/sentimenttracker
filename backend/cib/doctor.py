"""Setup checks: what is still a placeholder, and what that costs you.

`cib seed` deliberately creates placeholders rather than inventing data, which means a fresh
install looks like a working tool that measures nothing. This reports exactly what is missing,
what each gap makes unavailable, and the command that fixes it.

Findings are graded by consequence, not by tidiness:

  * **blocker** — a metric cannot be produced at all, or would be measured against the wrong
    thing. A campaign with a placeholder publication date is a blocker because every day_index is
    measured from it.
  * **warning** — figures are produced but incomplete, or something that should be watched is not.
  * **ok** — checked and healthy. Reported so the absence of a finding is not read as "not checked".
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from . import config
from .db import query, query_one
from .metrics import snapshot as snapshot_module
from .repo import campaigns as campaign_repo
from .repo import entities as entity_repo
from .repo import imports as import_repo
from .timeutil import parse_timestamp
from .watch import rules as rule_repo

Level = Literal["blocker", "warning", "ok"]

# Text that marks a record as still being scaffolding rather than a real one.
PLACEHOLDER_MARKERS = ("todo", "example.invalid", "replace with", "placeholder")

# The seed's stand-in dates. Real campaigns rarely publish exactly on 1 January, and the epoch is
# what an unfilled inbound-signal date looks like.
SUSPICIOUS_DATES = ("1970-01-01", "2019-01-01", "2020-01-01")


@dataclass(frozen=True)
class Finding:
    level: Level
    code: str
    title: str
    detail: str
    fix: str | None = None

    def to_dict(self) -> dict:
        return {
            "level": self.level, "code": self.code, "title": self.title,
            "detail": self.detail, "fix": self.fix,
        }


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, level: Level, code: str, title: str, detail: str, fix: str | None = None):
        self.findings.append(Finding(level, code, title, detail, fix))

    def of(self, level: Level) -> list[Finding]:
        return [f for f in self.findings if f.level == level]

    @property
    def blockers(self) -> list[Finding]:
        return self.of("blocker")

    @property
    def warnings(self) -> list[Finding]:
        return self.of("warning")

    def to_dict(self) -> dict:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "blockers": len(self.blockers),
            "warnings": len(self.warnings),
            "ok": len(self.of("ok")),
        }


def _looks_placeholder(value: str | None) -> bool:
    return bool(value) and any(m in value.lower() for m in PLACEHOLDER_MARKERS)


def _age_days(stamp: str | None) -> float | None:
    parsed = parse_timestamp(stamp) if stamp else None
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return (datetime.utcnow() - parsed).total_seconds() / 86400


# --------------------------------------------------------------------------- checks

def _check_campaigns(conn: sqlite3.Connection, report: Report) -> None:
    campaigns = campaign_repo.list_all(conn)
    if not campaigns:
        report.add("blocker", "no-campaigns", "No campaigns exist",
                   "There is nothing to measure.",
                   "cib seed   # or: cib campaign add --name ... --publisher ...")
        return

    placeholder = [c for c in campaigns
                   if _looks_placeholder(c.name) or _looks_placeholder(c.publisher_org)]
    if placeholder:
        report.add(
            "blocker", "placeholder-campaigns",
            f"{len(placeholder)} campaign(s) still have placeholder names",
            "These are scaffolding, not records of anything: "
            + ", ".join(c.slug for c in placeholder),
            "cib campaign add --name '<real name>' --publisher '<real publisher>' ... "
            "(and remove the seeded ones)",
        )

    suspicious = [c for c in campaigns
                  if c.published_at and c.published_at[:10] in SUSPICIOUS_DATES]
    if suspicious:
        report.add(
            "blocker", "placeholder-dates",
            f"{len(suspicious)} campaign(s) have a stand-in publication date",
            "Every day_index is measured from published_at, so every comparison is measured "
            "against the wrong day zero until this is corrected: "
            + ", ".join(f"{c.slug} ({c.published_at[:10]})" for c in suspicious),
            "cib campaign set-published <slug> <YYYY-MM-DDTHH:MM:SS>   "
            "# do this BEFORE importing coverage",
        )

    imprecise = [c for c in campaigns if c.published_at and not c.date_is_exact]
    if imprecise:
        report.add(
            "warning", "imprecise-dates",
            f"{len(imprecise)} campaign(s) have a publication date known only to the "
            f"{'/'.join(sorted({c.published_at_precision for c in imprecise}))}",
            "Day zero is assumed for these, so days to peak, half-life and any day-aligned "
            "comparison carry that error bar: "
            + ", ".join(f"{c.slug} ({c.published_at[:7]})" for c in imprecise),
            "cib campaign set-published <slug> <YYYY-MM-DDTHH:MM:SS>   # once the day is confirmed",
        )

    empty = [c for c in campaigns if c.published_at and not int(query_one(
        conn, "SELECT COUNT(*) AS n FROM articles WHERE campaign_id = ?", (c.id,))["n"])]
    if empty:
        report.add(
            "blocker", "no-coverage",
            f"{len(empty)} published campaign(s) have no imported coverage",
            "Their footprints are empty datasets, not measurements of low coverage: "
            + ", ".join(c.slug for c in empty),
            "cib import inspect --file <export> --source infomedia   # then: cib import infomedia ...",
        )

    for c in campaigns:
        if c.published_at is not None:
            continue
        rules = rule_repo.list_all(conn, campaign_id=c.id)
        enabled = [r for r in rules if r.enabled]
        if not enabled:
            report.add(
                "blocker", "unwatched-prepublication",
                f"Pre-publication campaign '{c.slug}' is not being watched",
                f"{len(rules)} rule(s) configured, none enabled. Day zero will not be caught, "
                "which is the one thing this campaign needs.",
                "cib watch list   # then: cib watch enable <id>",
            )


def _check_entities(conn: sqlite3.Connection, report: Report) -> None:
    own = entity_repo.own_company_candidates(conn)
    if not own:
        report.add("blocker", "no-own-company", "No own-company entity is configured",
                   "Every exposure metric — mentions, depth score, first mention — is unavailable.",
                   "cib entity add --name '<company>' --type own_company --alias '<legal name>'")
    elif len(own) > 1:
        report.add(
            "blocker", "ambiguous-own-company",
            f"{len(own)} entities are typed own_company",
            "Exposure metrics refuse rather than measure the wrong company: "
            + ", ".join(f"#{e.id} {e.name}" for e in own),
            "Keep one and add the others as aliases on it, then delete or retype them.",
        )
    else:
        entity = own[0]
        if _looks_placeholder(entity.name):
            report.add("blocker", "placeholder-own-company",
                       f"The own-company entity is still a placeholder ('{entity.name}')",
                       "It matches nothing, so every exposure figure is zero for the wrong reason.",
                       "cib entity add --name '<company>' --type own_company --alias '<legal name>'")
        elif not entity.alias_list:
            report.add(
                "warning", "own-company-no-aliases",
                f"'{entity.name}' has no aliases",
                "Only the exact name is matched. Legal names, trading names and common "
                "misspellings will be missed, so mention counts are a floor.",
                "cib entity add --name '<company>' --type own_company --alias 'X A/S' --alias 'X Ltd'",
            )
        else:
            report.add("ok", "own-company", f"Own-company entity '{entity.name}' configured",
                       f"{len(entity.alias_list)} alias(es).")

    if not entity_repo.list_all(conn, type="competitor"):
        report.add("warning", "no-competitors", "No competitor entities are configured",
                   "'Named alongside peers' has nothing to compare against.",
                   "cib entity add --name '<competitor>' --type competitor")


def _check_watch(conn: sqlite3.Connection, report: Report) -> None:
    rules = rule_repo.list_all(conn)
    if not rules:
        report.add(
            "warning", "no-watch-rules", "No watch rules are configured",
            "Watch rules do two separate jobs and neither is happening: an rss/sitemap rule "
            "detects the moment a campaign publishes, and a gdelt_query rule imports coverage on "
            "a schedule so a live campaign keeps being measured.",
            "cib watch add --name '<feed>' --type rss --pattern <url> --campaign <slug>   "
            "# and: cib watch add --type gdelt_query --pattern '\"<terms>\"' --campaign <slug>",
        )
        return

    bad_pattern = [r for r in rules if _looks_placeholder(r.pattern)]
    if bad_pattern:
        report.add(
            "blocker" if any(r.enabled for r in bad_pattern) else "warning",
            "placeholder-watch-patterns",
            f"{len(bad_pattern)} watch rule(s) point at a placeholder",
            "They cannot match anything: "
            + ", ".join(f"#{r.id} {r.pattern}" for r in bad_pattern),
            "Recreate them against the publisher's real feed, then `cib watch enable <id>`.",
        )

    failing = [r for r in rules if r.enabled and r.last_error]
    if failing:
        report.add("warning", "watch-rules-failing",
                   f"{len(failing)} enabled rule(s) failed on their last run",
                   "; ".join(f"#{r.id} {r.last_error}" for r in failing[:3]),
                   "cib watch poll   # to retry")

    if not any(r.rule_type == "gdelt_query" and r.enabled for r in rules):
        report.add(
            "warning", "no-automated-ingest",
            "No campaign has an automated coverage source",
            "Nothing imports coverage on a schedule, so a live campaign stops being measured "
            "between manual archive exports. GDELT needs no API key.",
            "cib watch add --type gdelt_query --pattern '\"<terms>\"' --campaign <slug> "
            "--name 'GDELT'",
        )

    from .watch.poller import last_run
    run = last_run(conn)
    if run is None:
        report.add("warning", "poller-never-run", "The poller has never run",
                   "'We saw nothing' is not distinguishable from 'nobody looked'.",
                   "cib watch poll   # or let the scheduled workflow do it")
    else:
        age = _age_days(run["started_at"])
        if age is not None and age > 2:
            report.add("warning", "poller-stale",
                       f"The poller last ran {age:.1f} days ago",
                       "Publication may already have happened without being caught.",
                       "Check the scheduled workflow is enabled and running.")
        else:
            report.add("ok", "poller", "The poller is running",
                       f"Last run {run['started_at']}.")


def _check_data_quality(conn: sqlite3.Connection, report: Report) -> None:
    outlets = query(conn, "SELECT reach_value FROM outlets")
    if outlets:
        with_reach = sum(1 for o in outlets if o["reach_value"] is not None)
        pct = round(100.0 * with_reach / len(outlets), 1)
        if with_reach == 0:
            report.add("warning", "no-reach", "No outlet has a sourced reach figure",
                       f"0 of {len(outlets)} outlets. Total reach is null by design — a reach "
                       "figure may only be stored with a named source.",
                       "cib outlet reach <id> --value N --source '<where it came from>'")
        elif pct < 50:
            report.add("warning", "low-reach-coverage",
                       f"Only {pct}% of outlets have a sourced reach figure",
                       "Total reach is a floor, not a total.",
                       "cib outlet list   # then: cib outlet reach <id> --value N --source ...")
        else:
            report.add("ok", "reach", f"{pct}% of outlets have a sourced reach figure", "")

    last_import = import_repo.last_import_at(conn)
    if last_import is None:
        report.add("blocker", "nothing-imported", "Nothing has ever been imported",
                   "Every footprint figure in the system is an empty dataset.",
                   "cib import inspect --file <export> --source infomedia")
    else:
        age = _age_days(last_import)
        if age is not None and age > 30:
            report.add("warning", "imports-stale",
                       f"The last import was {age:.0f} days ago",
                       "Live campaigns may have coverage nobody has brought in.",
                       "cib ingest   # automated sources; archive exports remain manual")

    if not int(query_one(conn, "SELECT COUNT(*) AS n FROM escalations")["n"]):
        report.add("warning", "no-escalations", "The escalation log is empty",
                   "Escalations are logged by hand, so an empty log means 'not recorded', not "
                   "'nothing happened'. The escalation chain is the most predictive part of the "
                   "model.",
                   "cib escalation add --campaign <slug> --type ... --source-url ... --severity N")

    stale_signals = query(conn, """
        SELECT COUNT(*) AS n FROM inbound_signals WHERE substr(occurred_at, 1, 10) IN
            ('1970-01-01')
    """)
    if int(stale_signals[0]["n"]):
        report.add("warning", "placeholder-signals",
                   f"{stale_signals[0]['n']} inbound signal(s) have a placeholder date",
                   "A signal with an invented date cannot be cited.",
                   "Correct them in the database, or delete and re-log with the real dates.")

    for campaign in campaign_repo.list_all(conn):
        if campaign.published_at is None:
            continue
        undated = int(query_one(
            conn, "SELECT COUNT(*) AS n FROM articles "
                  "WHERE campaign_id = ? AND day_index IS NULL", (campaign.id,))["n"])
        if undated:
            report.add("warning", "undated-articles",
                       f"{undated} article(s) in '{campaign.slug}' could not be dated",
                       "They are excluded from every day-truncated figure.",
                       f"cib campaign set-published {campaign.slug} <date>   # recomputes day_index")

        if snapshot_module.last_snapshot_at(conn, campaign.id) is None and campaign.status in (
            "live", "decaying"
        ):
            report.add("warning", "no-snapshots",
                       f"'{campaign.slug}' is {campaign.status} but has no daily snapshots", "",
                       "cib snapshot")


def _check_config(conn: sqlite3.Connection, report: Report) -> None:
    from .watch.notify import configured_channels, describe_channels

    channels = configured_channels()
    problems = [c for c, ok, _ in describe_channels() if not ok]
    if channels == ["stdout"]:
        report.add(
            "warning", "alerts-stdout-only",
            "Watch-hit alerts go to stdout only",
            "In a scheduled run that means the alert is written to a log nobody reads. For a "
            "campaign whose whole purpose is catching day zero, that is a silent failure.",
            "Set CIB_NOTIFY_CHANNELS=webhook and CIB_NOTIFY_WEBHOOK_URL in .env (and as a "
            "GitHub secret for the workflow). Verify with: cib watch test-alert",
        )
    elif problems:
        report.add("blocker", "alerts-misconfigured",
                   f"Alert channel(s) configured but unusable: {', '.join(problems)}",
                   "Hits will be recorded and the alert dropped.",
                   "cib watch test-alert   # shows what each channel is missing")
    else:
        report.add("ok", "alerts", f"Alert channels configured: {', '.join(channels)}", "")

    if config.get_bool("CIB_LLM_ROLES_ENABLED", False) and not config.get("ANTHROPIC_API_KEY"):
        report.add("warning", "llm-enabled-without-key",
                   "CIB_LLM_ROLES_ENABLED is on but ANTHROPIC_API_KEY is not set",
                   "Ambiguous roles silently keep their rule-based assignment.",
                   "Set ANTHROPIC_API_KEY in .env, or turn the flag off.")


def run(conn: sqlite3.Connection) -> Report:
    report = Report()
    _check_campaigns(conn, report)
    _check_entities(conn, report)
    _check_watch(conn, report)
    _check_data_quality(conn, report)
    _check_config(conn, report)
    return report
