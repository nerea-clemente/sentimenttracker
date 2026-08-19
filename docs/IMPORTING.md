# Importing coverage

Licensed archive databases — Infomedia, Factiva, Nexis — cannot be scraped, and their terms
prohibit it. This tool reads the exports a licensed user produces by hand. This page covers how to
produce each one and what survives the trip.

## Before you import: check the campaign's publication date

Every `day_index` is measured from `campaigns.published_at`. Importing coverage against a
placeholder date and correcting it afterwards means recomputing every offset:

```bash
cib campaign set-published <campaign> 2019-03-04T00:00:00
```

That command reindexes existing articles and rewrites the daily snapshots, so it is safe to run
late — but it is cheaper to get right first.

## Inspect before you import

```bash
cib import inspect --file data/imports/export.csv --source infomedia
```

This prints the file's headers, the guessed column mapping, a sample row, and which canonical
fields are unmapped. Nothing is written. The mapping the import actually applies is recorded on
the `imports` ledger row, so it can be reviewed later.

Canonical fields: `headline`, `published_at`, `url`, `outlet_name`, `outlet_domain`, `country`,
`language`, `byline`, `body_text`, `outlet_tier`.

`headline` and `published_at` are required. Override any guess with `--map field=column`.

## Infomedia (Danish coverage)

Export as CSV or Excel with full text included. Both the Danish and English column spellings are
recognised (`Overskrift`/`Headline`, `Dato`/`Date`, `Medie`/`Source`, `Brødtekst`/`Body`).

```bash
cib import infomedia --campaign <campaign> --file data/imports/infomedia.xlsx
```

Country and language default to `DK` and `da` because that is what an Infomedia licence covers.
Both are overridable with `--default-country` / `--default-language`, and any value present on the
row itself always wins.

Excel files need the ingest extra (`pip install -e '.[ingest]'`). CSV needs nothing.

## Factiva

Export in **Article Format** (RTF, HTML or plain text) to keep full text, or as CSV for metadata
only. The article-format parser reads Factiva's two-letter field codes — `HD` headline, `BY`
byline, `PD` date, `SN` source, `LA` language, `LP` lead, `TD` body — and splits articles on the
`Document XXXX` accession line.

```bash
cib import factiva --campaign <campaign> --file data/imports/factiva.rtf
```

Factiva's `RE` codes are regions, not countries, so **country is left null** rather than guessed.
Set it with `--default-country` if the whole export is single-country.

## Nexis / LexisNexis

Export with full text as RTF, HTML or TXT. The parser reads `LABEL: value` headers followed by a
`BODY:` section, and splits on `N of M DOCUMENTS` or `End of Document`. The outlet name and
publication date are read from the header block above the labels, where Nexis puts them.

```bash
cib import nexis --campaign <campaign> --file data/imports/nexis.html
```

Trailers (`LOAD-DATE:`, `GRAPHIC:`, copyright lines) are not swallowed into the body.

## Generic CSV

Anything else. The delimiter is sniffed; the mapping is guessed from the header row and can be
overridden per field.

```bash
cib import csv --campaign <campaign> --file data/imports/whatever.csv \
    --map headline=Title --map published_at="Publication Date" --map outlet_name=Publisher
```

## GDELT DOC 2.0

No API key needed. Good for seeing that a story has broken internationally, and how widely, within
minutes.

```bash
cib import gdelt --campaign <campaign> --query '"fishmeal" "aquaculture"' \
    --start 20190301000000 --end 20190430000000
```

Two limits, both recorded in the import notes rather than papered over:

- **GDELT returns no article body.** Rows imported from it cannot be scanned for entity mentions,
  and the exposure metrics report that gap instead of reading it as zero mentions.
- **Timestamps are GDELT's crawl time**, which can lag the outlet's own publication time.

The API caps a single call at 250 records.

## Media Cloud

Needs `MEDIACLOUD_API_KEY` in `.env`.

```bash
cib import mediacloud --campaign <campaign> --query fishmeal \
    --start 2019-03-01 --end 2019-04-30
```

Media Cloud returns outlet identity reliably but, under its terms, full text only for some
sources. The import notes record how many rows arrived with body text.

## RSS, Atom and news sitemaps

```bash
cib import feed --campaign <campaign> --url https://publisher.example/feed
```

Feed summaries are stored as body text where the feed supplies them, and are usually extracts
rather than full text. Feeds declaring a DTD are refused: no legitimate feed needs one, and a DTD
is how an entity-expansion bomb arrives.

The same parser backs the watch poller — see the README's watch section.

## After importing

```bash
cib cluster <campaign>       # re-run syndication detection (the import does this automatically)
cib entity match <campaign>  # detect entity mentions in whatever body text arrived
cib snapshot --all           # rebuild metrics_daily
```

## What gets rejected, and why

Rows are rejected rather than guessed at. The import report lists the first fifty reasons:

| Reason | What to do |
|---|---|
| `no headline` | The mapped headline column is empty for that row. |
| `no publication date` | The mapped date column is empty. |
| `unparseable publication date: '…'` | Add the format, or fix the export. Nothing is invented. |
| `no outlet name, outlet domain or URL to derive an outlet from` | Map an outlet column, or use `--map url=…` so the domain can be read from it. |

A rejected row is never partially imported.

## Re-importing

Re-running the same file is a no-op for the article table:

```
import #3 (manual) | 12 rows | 0 new | 12 already present | 0 rejected |
identical file previously imported as #1
```

The ledger row is still written, so a re-import that found nothing stays distinguishable from an
import nobody ran. Importing the same file into a *different* campaign does insert rows, and the
summary says so explicitly.
