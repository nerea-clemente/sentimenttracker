"""Factiva export mapper.

Factiva is a licensed archive and cannot be scraped. This reads the exports a licensed user
produces: the article-format text/RTF/HTML dump, where each article is a block of two-letter
field codes, and the tabular CSV export.

Field codes used (Factiva's own):
    HD headline · BY byline · WC word count · PD publication date · SN source name
    SC source code · LA language · CY copyright · LP lead paragraph · TD body text
    RE region · AN accession number · CO company codes · IN industry codes
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .base import ImportReport, run_import
from .csv_generic import read_rows, sniff
from .richtext import to_text

FIELD_CODES = {
    "HD": "headline", "BY": "byline", "WC": "word_count", "PD": "published_at",
    "SN": "outlet_name", "SC": "source_code", "LA": "language", "CY": "copyright",
    "LP": "lead", "TD": "body", "RE": "region", "AN": "accession", "SE": "section",
    "PG": "page", "VOL": "volume", "IN": "industry", "CO": "company", "NS": "subject",
    "IPD": "ipd", "PUB": "publisher", "AN_URL": "url", "ART": "art", "ET": "edition",
}

# A field code either carries its value on the same line ("HD  Some headline") or stands
# alone with the value on the lines that follow, which is how Factiva writes LP and TD.
_CODE_LINE = re.compile(r"^([A-Z]{2,3})(?:\s{2,}(.*))?\s*$")
_DOC_SPLIT = re.compile(r"^\s*Document\s+\S+\s*$", re.MULTILINE)
_URL_IN_TEXT = re.compile(r"https?://\S+")

# Factiva writes languages as words.
_LANGUAGES = {
    "english": "en", "danish": "da", "dansk": "da", "german": "de", "french": "fr",
    "spanish": "es", "portuguese": "pt", "norwegian": "no", "swedish": "sv",
    "dutch": "nl", "italian": "it", "chinese": "zh", "japanese": "ja",
}


def _blocks(text: str) -> list[str]:
    """Split a Factiva dump into per-article blocks.

    Factiva terminates each article with a `Document XXXX` accession line; where that is absent
    (some HTML exports), fall back to splitting on the HD field code.
    """
    parts = [b.strip() for b in _DOC_SPLIT.split(text) if b.strip()]
    if len(parts) > 1:
        return parts
    chunks = re.split(r"\n(?=HD\s{2,})", text)
    return [c.strip() for c in chunks if c.strip() and _CODE_LINE.search(c)]


def parse_block(block: str) -> dict:
    """Parse one Factiva article block into canonical fields."""
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for line in block.splitlines():
        match = _CODE_LINE.match(line)
        if match and match.group(1) in FIELD_CODES:
            current = FIELD_CODES[match.group(1)]
            value = (match.group(2) or "").strip()
            fields.setdefault(current, [])
            if value:
                fields[current].append(value)
        elif current and line.strip():
            fields[current].append(line.strip())

    def joined(key: str) -> str | None:
        vals = fields.get(key)
        return " ".join(v for v in vals if v).strip() or None if vals else None

    body_parts = [p for p in (joined("lead"), joined("body")) if p]
    language = (joined("language") or "").strip().lower()
    text_for_url = " ".join(fields.get("url", [])) or joined("body") or ""
    url_match = _URL_IN_TEXT.search(text_for_url)

    return {
        "headline": joined("headline"),
        "published_at": joined("published_at"),
        "outlet_name": joined("outlet_name"),
        "byline": joined("byline"),
        "language": _LANGUAGES.get(language, language[:2] or None),
        "country": None,   # Factiva's RE codes are regions, not countries; never guessed here
        "url": url_match.group(0).rstrip(".,);") if url_match else None,
        "body_text": "\n\n".join(body_parts) or None,
    }


def parse_text(text: str) -> list[dict]:
    return [row for row in (parse_block(b) for b in _blocks(text)) if row.get("headline")]


def inspect(path: str | Path, encoding: str = "utf-8") -> dict:
    p = Path(path)
    if p.suffix.lower() in (".csv", ".tsv", ".txt") and _looks_tabular(p, encoding):
        info = sniff(p, encoding)
        info["format"] = "csv"
        return info
    rows = parse_text(to_text(p, encoding))
    return {
        "path": str(p), "format": "article_blocks", "articles_found": len(rows),
        "sample_row": rows[0] if rows else None,
        "required_present": bool(rows and rows[0].get("headline") and rows[0].get("published_at")),
    }


def _looks_tabular(path: Path, encoding: str) -> bool:
    head = path.read_text(encoding=encoding, errors="replace")[:2000]
    if head.lstrip().startswith("{\\rtf") or "<html" in head[:200].lower():
        return False
    first = head.splitlines()[0] if head else ""
    return first.count(",") >= 3 or first.count("\t") >= 3 or first.count(";") >= 3


def import_file(
    conn: sqlite3.Connection,
    *,
    campaign_ref: str | int,
    path: str | Path,
    mapping: dict[str, str] | None = None,
    source: str = "factiva",
    default_tier: str = "national_general",
    default_country: str | None = None,
    default_language: str | None = None,
    encoding: str = "utf-8",
    notes: str | None = None,
) -> ImportReport:
    p = Path(path)
    if _looks_tabular(p, encoding) and p.suffix.lower() in (".csv", ".tsv"):
        resolved = mapping or sniff(p, encoding)["guessed_mapping"]
        rows = read_rows(p, resolved, encoding)
        applied = resolved
    else:
        rows = parse_text(to_text(p, encoding))
        applied = {"format": "factiva_article_blocks", **{v: v for v in FIELD_CODES.values()}}
    if not rows:
        raise ValueError(
            f"No articles found in {p.name}. Export from Factiva in 'Article Format' (RTF, HTML "
            "or plain text) or as CSV, and check the file is not empty."
        )
    return run_import(
        conn, campaign_ref=campaign_ref, source=source, rows=rows, file_path=p,
        mapping=applied, default_tier=default_tier, default_country=default_country,
        default_language=default_language, notes=notes,
    )
