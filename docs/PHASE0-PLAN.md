# Phase 0 — Plan, assumptions, open questions

Status: **awaiting approval**. No implementation code written yet.

---

## 1. Assumptions

These are the readings of the spec I intend to build to. Correct any that are wrong.

### A. Scope and environment
1. **Greenfield repo.** `nerea-clemente/sentimenttracker` is empty (no commits, no remote refs). The spec's
   "match the conventions of the existing media monitoring tool" therefore has nothing to match against in
   this repo. I will establish conventions here and document them; see open question Q1.
2. **Local-first, no cloud requirement.** SQLite file on disk under `data/`, gitignored. Everything runs
   with `python -m cib ...` and `npm run dev`. GDELT / Media Cloud / notification channels are optional
   add-ons that degrade gracefully when unconfigured.
3. **Single-user / small-team tool.** No auth, no multi-tenancy. `created_by` is a string identity taken
   from `--as` CLI flag or `CIB_ACTOR` env var, defaulting to the OS user. Write-path attribution is a
   provenance requirement, not a security boundary.
4. **Python 3.11**, stdlib `sqlite3` with hand-written SQL. No ORM (spec §10). Dataclasses as row types.

### B. Data-model readings
5. **`day_index` can be negative.** Pre-publication leaks and embargo-break coverage are real; a campaign's
   articles are not guaranteed to postdate `campaigns.published_at`. `metrics_daily` will therefore allow
   negative `day_index`, and comparison at `at_day_index=N` means "all rows with `day_index <= N`",
   including negatives. Day 0 = the publication date itself.
6. **`day_index` is computed in whole days on calendar dates, in a fixed timezone per campaign.** Cross-
   timezone article timestamps otherwise make day boundaries arbitrary. I will add
   `campaigns.timezone` (IANA, default `Europe/Copenhagen`) and store it as provenance for the alignment.
   *This is an addition to the spec's schema — flagging it explicitly.*
7. **"Unique stories" = clusters + unclustered articles.** "Unique outlets" = `COUNT(DISTINCT outlet_id)`
   over all articles pre-clustering. Syndication ratio = unique outlets ÷ unique stories, per spec §3.
8. **Reach is constrained at the database level.** `CHECK (reach_value IS NULL OR reach_source IS NOT NULL)`
   on `outlets`. This makes design principle #2 structurally enforced rather than a convention: an unsourced
   reach number cannot be inserted. `reach_is_estimated` + `reach_estimation_method` are required together.
9. **Sentiment is deliberately not a column on `articles`.** It goes in a separate `article_tone` table with
   a mandatory `evidence_sentence`. No metric function reads that table, and the compare API will not expose
   it. Making it a join rather than a column is what stops it drifting into the headline view.
10. **Deduplication never deletes.** Duplicates get a `cluster_id` and `is_original = 0`. Cluster membership
    carries the detection `method` and a `similarity` score so a cluster can be audited and manually split.

### C. Metric definitions I am pinning (these will go in README §Metrics)
11. **Peak day** = earliest day_index with the maximum `article_count`. Ties resolve to the earliest day.
12. **Half-life** = days from peak day until the first day where daily volume < 10% of peak volume, using
    a 3-day trailing mean to avoid a single quiet weekend triggering it. Returns `None` (not 0) if the
    campaign has not yet decayed. *The trailing-mean smoothing is my addition — flag if you want raw daily.*
13. **Days to 90% of cumulative volume** returns `None` for `status = 'live'` campaigns. A live campaign's
    cumulative total is not final, so the figure would be meaningless and falsely reassuring.
14. **Long-tail flag** = any article with `day_index > 30`.
15. **Depth score** is always returned as `{components: {subject: n, named_supplier: n, ...}, weighted_total: n}`.
    The API has no endpoint that returns the scalar alone.
16. **Total reach** is always returned as `{sum, outlets_with_reach, outlets_total, coverage_pct}`. Same rule:
    no endpoint returns the bare sum.

### D. Idempotent re-import (acceptance criterion)
17. Each import row gets a `row_fingerprint` = sha256 of the normalised source-row tuple. `imports` stores
    a `file_hash`. An article is uniquely keyed by `(campaign_id, url)` where url is present, else by
    `(campaign_id, outlet_id, body_hash, published_at)`. Re-importing the same file is a no-op that still
    writes an `imports` ledger row recording "0 new, N already present".

---

## 2. Open questions (answers change what I build)

**Q1 — Existing tool conventions.** Is there another repo (the "existing media monitoring tool") I should
read for naming, layout, or API conventions? If so, name it and I will `add_repo` it before Phase 1.
If not, I will set conventions here.

**Q2 — Seed data identities (§7).** The spec describes the three seed campaigns without naming them, which
I read as deliberate. I need to know which of these you want:
  (a) Placeholder names + `TODO` markers, no watch rules that hit real domains;
  (b) You supply the real names, publishers, domains, RSS/newsletter URLs, and the pre-publication signal
      dates (congressional testimony, trade-press pickup) and I encode them.
Watch rules for campaign 3 are useless without real domains, so this materially affects §6 and §7.

**Q3 — Build scope this session.** Phases 1–4 in one pass, or stop after Phase 1 (core + metrics + tests +
CLI) for review before the dashboard and watchers? Phases 2–4 are substantially more code.

**Q4 — Escalation `severity` scale.** The spec gives 1–5 but no anchors. Severity-weighted totals are only
defensible if the anchors are written down. I propose: 1 = statement of concern; 2 = formal query or
parliamentary question; 3 = buyer/retailer or certifier action; 4 = regulatory investigation opened;
5 = binding regulatory/customs measure or litigation filed. Confirm or replace.

---

## 3. Proposed schema

Migrations in `backend/cib/migrations/*.sql`, applied by a runner that records applied versions in
`schema_migrations`. All tables get `created_at TEXT NOT NULL` (ISO-8601 UTC) and `created_by TEXT NOT NULL`.

### 001_core.sql
```
campaigns(id, name, slug UNIQUE, publisher_org, campaign_type, status, published_at NULL,
          first_signal_at NULL, timezone NOT NULL DEFAULT 'Europe/Copenhagen',
          themes JSON, notes, created_at, created_by)
  CHECK campaign_type IN (journalism, ngo_report, coalition, regulatory)
  CHECK status IN (pre_publication, live, decaying, archived)
  CHECK (status = 'pre_publication') = (published_at IS NULL)   -- structural guard for §3 pre-pub mode

outlets(id, name, domain UNIQUE NULL, country, language, tier,
        reach_value NULL, reach_source NULL, reach_is_estimated INT NOT NULL DEFAULT 0,
        reach_estimation_method NULL, reach_as_of NULL,
        is_known_syndication_partner INT NOT NULL DEFAULT 0, created_at, created_by)
  CHECK tier IN (national_general, national_business, trade, regional, broadcast, wire,
                 aggregator, ngo, newsletter, blog)
  CHECK (reach_value IS NULL OR reach_source IS NOT NULL)
  CHECK (reach_is_estimated = 0 OR reach_estimation_method IS NOT NULL)

articles(id, campaign_id FK, outlet_id FK, url NULL, headline, published_at, day_index INT NULL,
         language, country, byline NULL, body_text NULL, body_hash NULL, word_count NULL,
         cluster_id FK NULL, is_original INT NOT NULL DEFAULT 1,
         import_id FK, row_fingerprint, retrieved_at, created_at, created_by)
  UNIQUE INDEX (campaign_id, url) WHERE url IS NOT NULL
  UNIQUE INDEX (campaign_id, outlet_id, body_hash, published_at) WHERE url IS NULL
  INDEX (campaign_id, day_index), INDEX (campaign_id, outlet_id)
  -- day_index is a stored, recomputable cache; `cib recompute day-index` rebuilds it after a
  -- campaign's published_at changes (which the watch system can do — see §6).

clusters(id, campaign_id FK, representative_article_id FK, method, member_count, created_at, created_by)
  CHECK method IN (hash, title_similarity, shingle, manual)
cluster_members(cluster_id FK, article_id FK, similarity REAL NULL, method, PRIMARY KEY(cluster_id, article_id))
  -- separate from articles.cluster_id so the *evidence* for each membership is auditable

entities(id, name, type, aliases JSON, created_at, created_by)
  CHECK type IN (own_company, competitor, ngo, certifier, customer, supplier, regulator)

mentions(id, article_id FK, entity_id FK, role, evidence_sentence NOT NULL, char_offset,
         confidence REAL NULL, classified_by, created_at, created_by)
  CHECK role IN (subject, named_supplier, named_buyer, quoted_response, passing_reference)
  CHECK classified_by IN (rule, llm, human)
  CHECK (classified_by <> 'llm' OR confidence IS NOT NULL)

escalations(id, campaign_id FK, occurred_at, escalation_type, actor_name, actor_type, description,
            source_url NOT NULL, severity INT CHECK BETWEEN 1 AND 5,
            verified_by NULL, verified_at NULL, created_at, created_by)
  CHECK escalation_type IN (legal_petition, regulatory_action, customs_measure, retailer_statement,
                            buyer_statement, parliamentary_question, certifier_response,
                            company_response, other)

inbound_signals(id, campaign_id FK NULL, occurred_at, channel, summary, logged_by, created_at, created_by)
  CHECK channel IN (journalist, customer, tender, investor, employee, other)

imports(id, source, file_name NULL, file_hash NULL, imported_at, row_count, rows_inserted,
        rows_skipped_duplicate, rows_rejected, notes, mapping JSON NULL, created_by)
  CHECK source IN (infomedia, factiva, nexis, gdelt, mediacloud, rss, manual)

article_tone(article_id PK FK, label, evidence_sentence NOT NULL, confidence, classified_by, created_at, created_by)
  -- secondary label only. No metrics function reads this table.

metrics_daily(campaign_id FK, date, day_index INT, article_count, cumulative_articles,
              unique_outlets, cumulative_unique_outlets, countries, languages,
              own_company_mentions, computed_at, PRIMARY KEY(campaign_id, date))
```

### 002_watch.sql
```
watch_rules(id, campaign_id FK NULL, rule_type, pattern, source_url NULL, enabled,
            promotes_to_live INT NOT NULL DEFAULT 0, last_polled_at NULL, notes, created_at, created_by)
  CHECK rule_type IN (keyword, phrase, byline, domain, rss, sitemap, gdelt_query)
watch_hits(id, rule_id FK, occurred_at, matched_url, matched_title, matched_excerpt, raw JSON,
           notified_at NULL, promoted INT NOT NULL DEFAULT 0, created_at)
  UNIQUE(rule_id, matched_url)   -- a poller re-run does not re-alert
```

### Provenance model
Rather than a loose `provenance` column on every table, provenance is structural:
- every `article` → `import_id` → `imports` row (source, file, hash, time, actor);
- every asserted number (`outlets.reach_value`) → mandatory `*_source`;
- every classification (`mentions`, `article_tone`) → `evidence_sentence` + `classified_by` + `confidence`;
- every external claim (`escalations`) → mandatory `source_url`;
- every row → `created_at` / `created_by`.

Every metric returns row IDs, so drill-down joins back to these. Nothing on screen is unsourceable.

---

## 4. Proposed repo layout

```
sentimenttracker/
├─ README.md                  # incl. plain-language definition of every metric (§10)
├─ pyproject.toml             # ruff + pytest config, deps
├─ Makefile                   # make setup / test / lint / seed / dev
├─ .env.example  .gitignore
├─ docs/
│  ├─ PHASE0-PLAN.md          # this file
│  ├─ METRICS.md              # long-form definitions + worked examples
│  └─ IMPORTING.md            # how to produce each export
├─ backend/
│  ├─ cib/
│  │  ├─ db.py  config.py  actor.py
│  │  ├─ migrations/{001_core.sql,002_watch.sql,runner.py}
│  │  ├─ models.py            # dataclasses only
│  │  ├─ repo/                # hand-written SQL, one module per aggregate
│  │  ├─ ingest/{base,csv_generic,infomedia,factiva,nexis,gdelt,mediacloud,rss}.py
│  │  ├─ dedup.py  entities.py
│  │  ├─ metrics/{result,footprint,velocity,exposure,escalation,compare,snapshot}.py
│  │  ├─ watch/{rules,poller,notify}.py
│  │  ├─ export/{tables,briefing}.py
│  │  ├─ api/main.py          # FastAPI, thin wrapper over metrics/
│  │  ├─ cli.py               # import | metrics | compare | seed | watch | snapshot | export
│  │  └─ seed.py
│  └─ tests/                  # pytest, fixtures build a temp SQLite from migrations
├─ web/                       # Next.js (App Router, TS), fetches FastAPI only
│  └─ src/app/{compare,campaign/[slug],escalations,watchlist,export}/
└─ data/                      # gitignored: cib.sqlite3, imports/
```

**Why FastAPI rather than Next.js API routes:** spec §5 requires "no metric logic duplicated in the
frontend". A Python HTTP layer over `cib.metrics` makes that structural — the web app cannot compute a
metric even if someone wanted it to. Next.js talks to `/api/*` on the FastAPI process only.

### The `MetricValue` contract
Every metric returns:
```python
@dataclass(frozen=True)
class MetricValue:
    value: float | int | None
    row_ids: list[int]            # article/mention/escalation ids that produced it
    row_table: str                # which table row_ids point at
    basis: dict | None            # denominators, components, coverage
    definition_key: str           # -> README/METRICS.md anchor
    computed_at: str
```
Drill-down in the UI is then a generic component over `row_ids`, not per-metric bespoke code.
`compare()` returns `{campaign_id: {metric_key: MetricValue}}` plus a `caveats: list[str]` block that
carries things like "campaign X has 0% outlet reach coverage" so the UI cannot render a comparison
without its warnings.

---

## 5. Phase plan

- **Phase 1 — core.** Migrations + runner, `db.py`, dataclasses, generic CSV importer with column mapping,
  dedup (hash → title token-set ≥0.9 within ±3d → shingling), full metrics module, `compare()` with
  pre-publication refusal, CLI (`import`, `metrics`, `compare`, `seed`), pytest suite incl. a hand-checked
  syndication fixture and a re-import idempotency test.
- **Phase 2 — real data.** Infomedia CSV/XLSX, Factiva & Nexis RTF/HTML/CSV mappers, alias entity matching
  + rule-based roles (LLM fallback behind a flag, off by default), escalation & inbound-signal CLI entry.
- **Phase 3 — dashboard.** FastAPI layer, then Next.js: comparison → detail → drill-down → escalation log.
  Data-quality banner component used on every view.
- **Phase 4 — watchers.** GDELT + Media Cloud + RSS/sitemap pollers, watch rules, pluggable notify
  (stdout / email / webhook), APScheduler daily snapshot, CSV + one-page briefing export.

## 6. Acceptance-criteria → test mapping

| Criterion | Test |
|---|---|
| Same day-index comparison, all figures drill down | `test_compare_truncates_at_day_index`, `test_every_metric_returns_row_ids` |
| Unique stories vs unique outlets separate; ratio correct | `test_syndication_hand_checked` over a fixed 12-article fixture |
| No-publication-date campaign cannot render footprint | `test_prepublication_refuses_footprint` (raises, not returns zeros) |
| No metric depends on unsourced numbers | DB CHECK constraints + `test_reach_requires_source` |
| Re-import creates no duplicates | `test_reimport_is_noop` |
| Briefing defensible line by line | `test_briefing_contains_provenance_for_every_figure` |

---

## 7. Phase 0 decisions (approved)

| Question | Decision |
|---|---|
| Q1 Existing tool conventions | **None.** Greenfield; conventions established here and documented in README. |
| Q2 Seed data | **Placeholders + TODO markers.** Three campaign rows with descriptive placeholder names, no real domains, watch rules created but `enabled = 0`. Real identities supplied later via CLI. |
| Q3 Build scope | **All four phases in one pass.** |
| Q4 Severity anchors | **Proposed scale adopted:** 1 statement of concern · 2 formal query / parliamentary question · 3 buyer, retailer or certifier action · 4 regulatory investigation opened · 5 binding regulatory/customs measure or litigation filed. |

Additions to the spec schema, flagged in §1 and accepted as part of this plan:
`campaigns.timezone`, `cluster_members` table, `article_tone` table, `articles.day_index` cache,
`outlets.reach_estimation_method` / `reach_as_of`, `imports.file_hash` / row counters,
DB-level CHECK constraints enforcing the "no unsourced numbers" principle.
