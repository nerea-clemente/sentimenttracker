# Campaign Impact Benchmarker

A local-first tool that measures the media footprint of external campaigns and investigations
affecting a company in the aquaculture feed sector, and makes different campaigns directly
comparable against each other.

It exists to answer one question with evidence rather than judgement:

> **When a new investigation lands, how big is it compared to campaigns we have already lived
> through, and how fast is it escalating?**

It replaces qualitative "low / medium / high" ratings with measured, auditable values.

---

## The three design principles

These are not conventions. Each one is enforced by the schema, the type system, or the test suite,
because a principle that depends on everyone remembering it is a principle that will be broken on
a busy afternoon.

### 1. Every number is traceable

Every metric returns a `MetricValue` carrying the row IDs that produced it, the table they point
at, its denominators, and its plain-language definition:

```python
MetricValue(
    key="unique_stories",
    value=8,
    row_table="articles",
    row_ids=(1, 5, 6, 8, 9, 10, 11, 12),
    basis={"clustered_articles": 6, "unclustered_articles": 6, ...},
)
```

The dashboard's drill-down is one generic component over `row_ids`, not per-metric bespoke code,
so a new metric is drillable the moment it exists. `/api/evidence?table=articles&ids=…` resolves
those IDs to the source records with their import provenance — which file, imported when, by whom.

A test asserts that every metric with a value also carries source rows, and that those rows exist.
**If a value cannot be traced to an imported record, it is not displayed.**

### 2. No invented data

Reach and audience figures are stored only when they come from a named source. This is a database
constraint, not a code convention:

```sql
CHECK (reach_value IS NULL OR reach_source IS NOT NULL)
CHECK (reach_is_estimated = 0 OR reach_estimation_method IS NOT NULL)
```

An estimate must record its method. Every total reach figure is returned alongside the percentage
of outlets that actually have a sourced figure, so the coverage gap is impossible to miss.

Where a metric cannot be computed it returns a **stated reason**, never a misleading zero:

```
Half-life                 not available — Daily volume has not fallen below 10% of peak
                          (0.2 articles/day) anywhere up to day 3, the day 3 cutoff. The
                          campaign may well have decayed after it; widen the cutoff to find out.
```

### 3. Sentiment is not the headline metric

Coverage about labour or environmental abuse is negative by construction, so a sentiment score
does not discriminate between a trade story and a crisis. Tone is stored in its own table
(`article_tone`) with a mandatory supporting sentence — never as a column on `articles`, and never
in the comparison view. A test asserts that no metrics module reads that table.

---

## Quick start

```bash
make setup            # venv, dependencies, .env, database
make seed             # three placeholder campaign records
make test             # 131 tests
```

Then, in two terminals:

```bash
make api              # HTTP API on :8000
make web              # dashboard on :3000
```

The core tool — migrations, import, dedup, metrics, compare, CLI — is **stdlib-only** and needs no
third-party install and no cloud service. FastAPI, openpyxl and APScheduler are optional extras
for the API, Excel imports and the scheduler respectively.

---

## Importing coverage

Licensed archive databases cannot be scraped, so the tool reads the exports a licensed user
produces by hand.

```bash
# See a file's columns and the guessed mapping before importing anything
cib import inspect --file data/imports/export.csv --source infomedia

# Import it, overriding the mapping where the guess is wrong
cib import infomedia --campaign c2019 --file data/imports/export.csv \
    --map headline=Overskrift --map published_at=Dato

cib import factiva --campaign c2019 --file data/imports/factiva.rtf
cib import nexis   --campaign c2019 --file data/imports/nexis.html
cib import csv     --campaign c2019 --file data/imports/anything.csv
```

API-based sources, for cross-country volume rather than full text:

```bash
cib import gdelt      --campaign c2019 --query '"fishmeal" "aquaculture"' --start 20190301000000
cib import mediacloud --campaign c2019 --query fishmeal --start 2019-03-01 --end 2019-04-30
cib import feed       --campaign c2019 --url https://publisher.example/feed
```

**Re-importing the same file is a no-op.** An article is keyed by `(campaign, url)`, falling back
to `(campaign, outlet, body hash, publication time)` for exports with no URL. The re-import still
writes a ledger row, so "we checked and nothing changed" stays distinguishable from "nobody
checked".

### Deduplication

Three passes, cheapest and most certain first:

1. **Exact body hash** — identical text, certain.
2. **Normalised-title token-set ratio ≥ 0.9** within a ±3 day window.
3. **Body shingle overlap ≥ 0.7** within the same window, for lightly edited republication.

Nothing is ever deleted. A duplicate keeps its row, gains a `cluster_id`, and has `is_original`
set to 0; the `cluster_members` row records which method matched, at what score, and against which
article — so a cluster can be audited and split by hand.

This is why **unique stories and unique outlets are reported separately**. Forty outlets carrying
one wire story is forty outlets and one story, and reporting only one of those numbers is how a
wire pickup gets mistaken for a wave of independent interest.

---

## Comparing campaigns

```bash
cib compare c2019 c2022 --at-day 5
```

```
Comparison at day 5 (explicit cutoff)

  Metric                                           c2019                 c2022
  ----------------------------------------------------------------------------
  Unique stories                                       5                     5
  Unique outlets                                       8                     8
  Syndication ratio                                  1.6                   1.6
  ...
```

Every campaign is truncated at the **same day index**, so a live campaign at day 5 is compared
against past campaigns at *their* day 5, not their lifetime totals. An unfair comparison is worse
than no comparison, so:

- One selector decides what "the articles for this campaign at day N" means, and every metric uses
  it. There is no per-metric truncation to get subtly wrong.
- Omitting `--at-day` does **not** compare lifetimes. It uses the shortest observed window across
  the selected campaigns and says so in the caveats.
- A campaign whose coverage ends before the cutoff is flagged, not silently compared.

### Pre-publication campaigns

A campaign with no publication date has no footprint and **cannot be given one**:

```bash
$ cib metrics forthcoming-investigation
Campaign 'forthcoming-investigation' has no publication date. Footprint metrics are not
defined for it, and a zero here would read as low risk. Use the pre-publication view instead.
```

`campaign_metrics()` raises, the API returns **409**, and the comparison view separates such
campaigns out of the table entirely. What it shows instead is the only evidence that actually
informs a pre-publication assessment:

- **Publisher precedent** — the measured footprint of the same publisher's previous
  investigations. A precedent with nothing imported is marked as unmeasured, not shown as zeros.
- **Logged internal signals** — journalist enquiries, customer questionnaires, tender questions,
  investor queries.
- **Watch rules** — with a red banner when none is enabled, because a watchlist that looks healthy
  while watching nothing is the worst possible state.

The schema enforces the pairing so it cannot drift:

```sql
CHECK ((status = 'pre_publication') = (published_at IS NULL))
```

---

## Metrics

Full definitions, with what each metric counts and what it cannot tell you, are in
**[docs/METRICS.md](docs/METRICS.md)** — generated from `backend/cib/metrics/definitions.py`, so
the documentation cannot drift from the engine. Run `cib definitions` for the same text in the
terminal, or open **Metric definitions** in the dashboard.

| Metric | In one line |
|---|---|
| **Unique stories** | Distinct pieces of journalism, after collapsing syndicated republication. |
| **Unique outlets** | Separate publications that carried it, counting republication as a real appearance. |
| **Total articles** | Every imported item, duplicates included. |
| **Syndication ratio** | Outlets per story. High means wire pickup, not independent interest. |
| **Outlet tier mix** | Where the coverage sits: national, business, trade, regional, broadcast, wire. |
| **Countries** / **Languages** | How far it spread, with the unknown share reported alongside. |
| **Total reach where sourced** | Summed audience of outlets with a *named-source* figure. |
| **Outlets with a known reach value** | The honesty check on the line above. |
| **Peak day** / **Peak day volume** / **Days to peak** | When it was loudest. Ties resolve to the earlier day. |
| **Half-life** | Days from peak until daily volume stays below a tenth of peak. |
| **Days to 90% of volume** | How long to accumulate nine tenths of the coverage. Refuses for live campaigns. |
| **Long tail** | Whether anything appeared after day 30. |
| **Own-company mentions** | How often we are named, split by role. |
| **Exposure depth** | How *centrally* we appear. Never returned without its components. |
| **First mention** | The day we were first named. |
| **Named alongside peers** | More, less, or alongside comparable companies. |
| **Escalations** | Downstream consequences, by type. |
| **Days to first escalation** | The sharpest early signal that a story becomes a problem. |
| **Severity-weighted escalation total** | Weighted by the 1–5 anchors below. |
| **Escalation chain** | How far it travelled: publication → NGO → institutional → market → regulatory. |

### Day index

`day_index` is what makes campaigns comparable: whole days from publication, on calendar dates in
the campaign's own timezone.

- **Day 0 is publication day**, not the day after.
- **It can be negative.** Embargo breaks and pre-publication leaks are real coverage; clamping them
  to zero would hide exactly the thing worth noticing.
- Articles whose date cannot be parsed are **excluded** from every day-truncated figure and
  reported as a data-quality number — never folded silently into day 0.

### Exposure depth

Being the subject of a story is not the same as being listed in passing:

| Role | Weight |
|---|---|
| subject | ×5 |
| named supplier | ×3 |
| named buyer | ×3 |
| quoted response | ×2 |
| passing reference | ×1 |

The weighted total is **never** returned alone — the API has no endpoint that does so. The
components are what make it arguable, and "we scored 34" is exactly the kind of number this tool
exists to replace.

### Escalation severity anchors

A severity-weighted total is only defensible if the scale is written down:

| Severity | Meaning |
|---|---|
| 1 | Statement of concern |
| 2 | Formal query or parliamentary question |
| 3 | Buyer, retailer or certifier action |
| 4 | Regulatory investigation opened |
| 5 | Binding regulatory or customs measure, or litigation filed |

---

## Entity matching

Alias-based rule matching first, always. The rules are readable, deterministic, cheap to re-run,
and every mention they produce carries the sentence that justifies it — `evidence_sentence` is
`NOT NULL` by schema, because a mention you cannot quote back is a mention you cannot defend.

```bash
cib entity add --name "Nordisk Aqua Feed" --type own_company \
    --alias "Nordisk Aqua Feed A/S" --alias "Nordisk Fôr"
cib entity match c2019
```

Guards worth knowing about:

- Aliases shorter than three characters are **refused**. A two-letter alias matches inside
  unrelated words often enough to poison the counts, and a poisoned count is worse than a missing
  one.
- Overlapping surface forms collapse to one mention, so a nested alias does not double-count.
- Articles with **no body text cannot be scanned**, and that gap is reported as a caveat rather
  than being read as zero mentions. GDELT and most Media Cloud rows fall into this category.
- If more than one entity is typed `own_company`, exposure metrics **refuse** rather than silently
  measuring the wrong company. Additional legal or trading names belong on one entity as aliases.

An LLM is used only as a fallback for **role assignment** on mentions the rules already found and
already have a sentence for. It never finds mentions and never produces a number. It is off unless
`CIB_LLM_ROLES_ENABLED=1`, and anything it decides is stored with `classified_by='llm'` and a
confidence, so the whole set can be excluded with one `WHERE` clause.

---

## Watch and trigger system

For a campaign that has not yet published, the tool's job is to catch day zero.

```bash
cib watch add --name "Publisher feed" --type rss \
    --pattern https://publisher.example/feed --campaign forthcoming --promotes-to-live
cib watch poll                  # one pass; safe to run from cron
cib watch run                   # long-running poller + daily snapshot (needs the watch extra)
cib snapshot                    # write metrics_daily rows for live campaigns
```

Rule types: `keyword`, `phrase`, `byline`, `domain`, `rss`, `sitemap`, `gdelt_query`.

- A re-polled item **never re-alerts**: hits are unique on `(rule, url)`.
- Notification channels are pluggable via `CIB_NOTIFY_CHANNELS`: `stdout`, `file`, `webhook`,
  `email`. A failing channel never stops a hit being recorded.
- **Promotion**: when a rule marked `promotes_to_live` hits, the campaign's `published_at` is set
  to the matched item's time, every article's `day_index` is recomputed, and daily snapshotting
  begins. It happens once, and the hit row records the moment for later audit.
- `watch_runs` logs every poll, so "we saw nothing" stays distinguishable from "the poller was not
  running" — the dashboard shows a red banner for the latter.

---

## Automated ingestion

Archive coverage is licensed and arrives by hand. Everything else can be pulled on a schedule.

```bash
# Tell the tool what to watch for, per campaign
cib watch add --campaign c2019 --name "GDELT: fishmeal" \
    --type gdelt_query --pattern '"fishmeal" "aquaculture"'

cib ingest --dry-run    # what would be fetched, calling no API
cib ingest              # import it
```

`cib ingest` is what the scheduled workflow runs. For every **enabled** rule naming an automated
source it fetches the window since that rule last ran and imports it into the rule's campaign,
then re-clusters anything that changed.

**Detection and measurement are separate jobs.** `cib watch poll` records that something published
and can set a campaign's day zero; it does not bring the coverage in. `cib ingest` does that. A
feed rule is treated as detection-only unless its notes contain the word `ingest`, because
importing everything a publisher's feed carries would bury a campaign in unrelated articles.

Things it does deliberately:

- **The watermark advances only on success.** A failed fetch keeps its window so the next run
  re-fetches it, rather than leaving a hole nothing would ever notice.
- **Windows overlap by 24 hours**, because GDELT's crawl time lags publication. Re-importing is
  free — articles are keyed by `(campaign, url)`.
- **A dormant rule cannot ask for an unbounded window.** `--lookback-days` is a hard floor, so a
  rule idle for a year requests a week rather than a year of which GDELT would return an
  arbitrary 250 articles.
- **Hitting GDELT's 250-record cap is reported**, because a capped response means coverage was
  silently dropped.
- **GDELT country names are mapped to ISO alpha-2.** GDELT says `Denmark`; everything else here
  stores `DK`. An unmapped country becomes null and shows up in the data-quality gap — a missing
  country is recoverable, a wrong one quietly inflates the country count.
- **GDELT supplies no article body**, so those rows cannot be scanned for entity mentions and the
  exposure metrics report that as a floor rather than assuming zero.

## Exports

```bash
cib export comparison c2019 c2022 --at-day 5 --out comparison.csv
cib export articles c2019 --out evidence.csv
cib export briefing c2019 c2022 --at-day 5 --format html --out briefing.html
```

Every CSV carries a provenance header: when it was generated, by whom, at which cutoff, and the
data-quality figures for the campaigns involved. A CSV that circulates without those is a CSV that
gets quoted without them.

The **briefing** is a one-page document for a crisis or leadership team, and is built to be
defended line by line: every figure appears with its plain-language definition, its source row
count and its caveats, followed by an evidence appendix of the actual article rows with links and
import provenance. The HTML version is fully self-contained and prints.

---

## CLI reference

```
cib init | seed
cib campaign list | add | set-published | precedent
cib import inspect | csv | infomedia | factiva | nexis | feed | gdelt | mediacloud | list
cib ingest [--dry-run] [--lookback-days N]
cib cluster <campaign>
cib entity add | list | match
cib escalation add | verify | list
cib signal --campaign … --channel … --summary …
cib outlet list | reach
cib metrics <campaign> [--at-day N]
cib compare <campaign> <campaign> [--at-day N]
cib definitions
cib snapshot [--all]
cib watch list | add | enable | poll | hits | run
cib export comparison|articles|escalations|mentions|briefing
cib serve
```

Global flags: `--db PATH`, `--as NAME` (recorded in `created_by` on every write), `--json`.

---

## Repository layout

```
backend/cib/
  migrations/     numbered .sql + runner; applied migrations are checksummed
  repo/           hand-written SQL, one module per aggregate
  ingest/         csv_generic · infomedia · factiva · nexis · gdelt · mediacloud · feeds
  metrics/        definitions · result · selectors · footprint · velocity ·
                  exposure · escalation · comparison · snapshot
  watch/          rules · poller · notify
  export/         tables (CSV) · briefing (Markdown/HTML)
  api/            FastAPI — computes nothing itself
  dedup.py  entities.py  cli.py  seed.py
backend/tests/    pytest; fixtures build a real database from the real migrations
web/              Next.js dashboard; talks only to the API
docs/             METRICS.md (generated) · PHASE0-PLAN.md
scripts/          render_metrics_doc.py
```

**No metric logic lives outside `backend/cib/metrics/`.** The HTTP layer and the dashboard consume
it; they do not reimplement any part of it. A test compares the API's output to the module's
output field by field.

---

## Conventions

- Python 3.11, type hints, `ruff`, `pytest`. **No ORM** — this database will be queried by hand by
  people who need to defend a number in a meeting, so the schema and the queries stay legible.
- Every table carries `created_at` and `created_by`. Every write path records who or what wrote it;
  a test asserts it.
- Secrets live in `.env`, which is gitignored. `.env.example` documents every key, and a test
  fails if the code reads a key the example does not mention.
- `docs/METRICS.md` is generated. Edit `definitions.py` and run `make docs`; a test fails if the
  checked-in file is stale.

## Publishing to GitHub Pages

The dashboard normally talks to the Python API. GitHub Pages serves static files only, so there is
no API to talk to — the same problem the media tracker solves, and solved the same way here:

```
cib export snapshot   →  web/src/lib/seed.json  →  next build (output: "export")  →  Pages
```

`cib export snapshot` writes every figure the dashboard needs into one JSON file, computed by
`cib.metrics` — the same functions the CLI, the API and the tests call. The static frontend
*selects* from what was baked; it still computes nothing. A test asserts that every baked value
equals what `campaign_metrics()` returns for the same campaign and cutoff, and that every
`row_ids` entry resolves in the baked evidence index, so the drill-down works offline too.

What the static build can and cannot do:

| | Live (`make api` + `make web`) | Static (Pages) |
|---|---|---|
| Comparison, detail, drill-down | yes | yes |
| Day-index cutoffs | any | the baked ones: 7, 14, 30, 90, lifetime |
| Campaigns per comparison | 2–4 | 2–4 (2–3 above 5 campaigns) |
| Per-campaign CSV + briefing | yes | yes, pre-generated as files |
| Comparison CSV / multi-campaign briefing | yes | no — depends on the reader's selection |
| Logging escalations, toggling watch rules | yes | no, and the UI says so |

Every page of a static build carries a banner with the snapshot's generation time, because a
read-only build that looks live is how someone quotes a three-week-old number.

### The workflow

`.github/workflows/pages.yml` mirrors the media tracker's `refresh.yml`: on a schedule it polls the
watch rules, rebuilds the daily snapshots, exports `seed.json`, commits the state database back to
the branch, builds the site and deploys it. On a push it skips the pipeline and just rebuilds from
the committed snapshot. Lint, the docs-sync check and the test suite run before anything deploys —
a snapshot built from code that fails its own tests is not evidence.

**Enable it once:** repository → Settings → Pages → *Source: GitHub Actions*. The site then lands at
`https://<owner>.github.io/sentimenttracker/`. The base path is set by `DEPLOY_BASE_PATH` in the
workflow; change it if you rename the repository.

Build it locally exactly as CI does:

```bash
make snapshot      # rebuild seed.json from state/cib.sqlite3
make site          # static export with the Pages base path
make site-serve    # → http://127.0.0.1:4011/sentimenttracker/
```

### What is committed, and what must not be

`state/cib.sqlite3` **is** committed — that is how scheduled runs persist state between them, and
it is what the Pages build snapshots. It carries the seeded placeholder records and no imported
coverage.

Your working database lives in `data/` and is gitignored. **Keep it that way.** This repository is
public, and real campaign names, escalation logs and logged internal signals are commercially
sensitive. A test fails the build if the committed snapshot ever contains imported articles.

To preview the dashboard with data in it, use the synthetic fixture:

```bash
make demo-snapshot   # builds data/demo.sqlite3 from the test fixture and snapshots it
make snapshot        # restore the real one before committing
```

That snapshot labels itself `SYNTHETIC DEMO DATA` on every page. The articles in it are invented
for the test suite — which is exactly why they may never be presented as measurement.

## Seed data

`cib seed` creates three campaign records — two archived baselines and one pre-publication — plus
an own-company entity, watch rules and precedent links.

Everything in it is a **placeholder marked TODO**, and no article data is invented:

- The two archived campaigns are created **empty**. Their footprints stay unmeasured until real
  archive exports are imported, and the empty state says so explicitly rather than rendering as
  low coverage.
- Watch rules are created **disabled**, because a rule pointing at a placeholder domain would poll
  nothing while looking perfectly healthy.
- Publication dates on the archived campaigns are placeholders. Correct them with
  `cib campaign set-published` **before** importing coverage — every `day_index` is measured from
  them.
