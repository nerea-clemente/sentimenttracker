"""Generic CSV/TSV mapper with column mapping.

Anything that is not one of the named archive exports comes through here. The column mapping is
either supplied explicitly (by the CLI flag or the frontend mapping UI) or guessed from the header
row, and either way it is stored on the import ledger row so the mapping used can be reviewed
later.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from pathlib import Path

from .base import CANONICAL_FIELDS, ImportReport, run_import

# Header spellings seen in the wild, per canonical field. Guessing is a convenience only: the
# mapping actually applied is always recorded on the import row.
HEADER_HINTS: dict[str, tuple[str, ...]] = {
    "headline": ("headline", "title", "head", "overskrift", "rubrik", "article title", "hd"),
    "published_at": ("published_at", "published", "date", "publication date", "pubdate",
                     "dato", "publiceret", "publication_date", "pd", "datetime"),
    "url": ("url", "link", "web url", "article url", "permalink", "an_url"),
    "outlet_name": ("outlet", "outlet_name", "source", "publication", "media", "medie",
                    "source name", "sn", "kilde"),
    "outlet_domain": ("domain", "outlet_domain", "host", "hostname", "website", "site"),
    "country": ("country", "land", "country code", "re", "region"),
    "language": ("language", "lang", "sprog", "la"),
    "byline": ("byline", "author", "journalist", "forfatter", "by", "reporter"),
    "body_text": ("body", "body_text", "text", "content", "full text", "fulltext",
                  "article text", "brødtekst", "lp", "td"),
}


def guess_mapping(headers: list[str]) -> dict[str, str]:
    """Map canonical field -> source column, from header spellings. Unmapped fields are omitted."""
    lowered = {h.strip().lower(): h for h in headers if h}
    mapping: dict[str, str] = {}
    for field, hints in HEADER_HINTS.items():
        for hint in hints:
            if hint in lowered:
                mapping[field] = lowered[hint]
                break
    return mapping


def sniff(path: str | Path, encoding: str = "utf-8-sig") -> dict:
    """Inspect a delimited file without importing it: headers, guessed mapping, a sample row.

    This is what the column-mapping UI calls before the user confirms an import.
    """
    text = Path(path).read_text(encoding=encoding, errors="replace")
    delimiter = _sniff_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = [h for h in (reader.fieldnames or []) if h]
    sample = next(reader, None)
    guessed = guess_mapping(headers)
    return {
        "path": str(path),
        "delimiter": delimiter,
        "headers": headers,
        "guessed_mapping": guessed,
        "unmapped_canonical_fields": [f for f in CANONICAL_FIELDS if f not in guessed],
        "sample_row": dict(sample) if sample else None,
        "required_present": all(f in guessed for f in ("headline", "published_at")),
    }


def _sniff_delimiter(text: str) -> str:
    head = "\n".join(text.splitlines()[:5])
    try:
        return csv.Sniffer().sniff(head, delimiters=",;\t|").delimiter
    except csv.Error:
        # Fall back to whichever candidate appears most often in the header line.
        first = head.splitlines()[0] if head else ""
        return max(",;\t|", key=first.count)


def read_rows(path: str | Path, mapping: dict[str, str],
              encoding: str = "utf-8-sig") -> list[dict]:
    """Apply a mapping to a delimited file, producing canonical rows."""
    text = Path(path).read_text(encoding=encoding, errors="replace")
    delimiter = _sniff_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows = []
    for source_row in reader:
        rows.append({
            field: source_row.get(column)
            for field, column in mapping.items()
            if field in CANONICAL_FIELDS
        })
    return rows


def import_file(
    conn: sqlite3.Connection,
    *,
    campaign_ref: str | int,
    path: str | Path,
    mapping: dict[str, str] | None = None,
    source: str = "manual",
    default_tier: str = "blog",
    default_country: str | None = None,
    default_language: str | None = None,
    encoding: str = "utf-8-sig",
    notes: str | None = None,
) -> ImportReport:
    resolved_mapping = mapping or sniff(path, encoding)["guessed_mapping"]
    missing = [f for f in ("headline", "published_at") if f not in resolved_mapping]
    if missing:
        raise ValueError(
            f"Cannot import {path}: no column mapped to {', '.join(missing)}. "
            "Run `cib import inspect` to see the headers and pass --map field=column."
        )
    rows = read_rows(path, resolved_mapping, encoding)
    return run_import(
        conn,
        campaign_ref=campaign_ref,
        source=source,
        rows=rows,
        file_path=path,
        mapping=resolved_mapping,
        default_tier=default_tier,
        default_country=default_country,
        default_language=default_language,
        notes=notes,
    )
