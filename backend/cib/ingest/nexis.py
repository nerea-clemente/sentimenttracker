"""Nexis / LexisNexis export mapper.

Nexis is a licensed archive and cannot be scraped. This reads the RTF, HTML, DOCX-as-HTML or
plain-text exports a licensed user produces, where each article is a block of `LABEL: value`
headers followed by a BODY section, and the tabular CSV export.

Typical block shape:

        Copyright 2019 Example Media Ltd
                    The Example Times

    March 12, 2019 Tuesday

    SECTION: Business; Pg. 14
    LENGTH: 812 words
    HEADLINE: Feed supplier named in West Africa investigation
    BYLINE: Jane Doe
    BODY:
    ...
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .base import ImportReport, run_import
from .csv_generic import read_rows, sniff
from .richtext import to_text

LABELS = {
    "HEADLINE": "headline", "TITLE": "headline",
    "BYLINE": "byline", "AUTHOR": "byline",
    "SECTION": "section", "LENGTH": "length", "LANGUAGE": "language",
    "PUBLICATION": "outlet_name", "SOURCE": "outlet_name", "NEWSPAPER": "outlet_name",
    "PUBLICATION-TYPE": "publication_type", "COUNTRY": "country",
    "LOAD-DATE": "load_date", "DATELINE": "dateline", "BODY": "body_text",
    "GRAPHIC": "graphic", "URL": "url", "DOCUMENT-TYPE": "document_type",
    "COPYRIGHT": "copyright", "DISTRIBUTION": "distribution",
}
_TERMINATORS = ("LOAD-DATE:", "GRAPHIC:", "PUBLICATION-TYPE:", "JOURNAL-CODE:",
                "DOCUMENT-TYPE:", "Copyright ", "End of Document")

_LABEL_LINE = re.compile(r"^([A-Z][A-Z\- ]{2,30}):\s*(.*)$")
# Nexis numbers documents "1 of 47 DOCUMENTS" or separates them with "End of Document".
_DOC_SPLIT = re.compile(
    r"^\s*(?:\d+\s+of\s+\d+\s+DOCUMENTS?|End of Document)\s*$", re.MULTILINE | re.IGNORECASE
)
_DATE_LINE = re.compile(
    r"^\s*((?:January|February|March|April|May|June|July|August|September|October|November|"
    r"December)\s+\d{1,2},\s+\d{4}|\d{1,2}\s+(?:January|February|March|April|May|June|July|"
    r"August|September|October|November|December)\s+\d{4})",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://\S+")

_LANGUAGES = {
    "english": "en", "danish": "da", "german": "de", "french": "fr", "spanish": "es",
    "portuguese": "pt", "norwegian": "no", "swedish": "sv", "dutch": "nl", "italian": "it",
}


def _blocks(text: str) -> list[str]:
    parts = [b.strip() for b in _DOC_SPLIT.split(text) if b.strip()]
    if len(parts) > 1:
        return parts
    chunks = re.split(r"\n(?=\s*HEADLINE:)", text)
    return [c.strip() for c in chunks if "HEADLINE:" in c]


def parse_block(block: str) -> dict:
    """Parse one Nexis article block into canonical fields."""
    lines = block.splitlines()
    fields: dict[str, list[str]] = {}
    current: str | None = None
    outlet_guess: str | None = None
    date_guess: str | None = None

    for raw_line in lines:
        line = raw_line.rstrip()
        match = _LABEL_LINE.match(line.strip())
        if match and match.group(1).upper() in LABELS:
            label = match.group(1).upper()
            current = LABELS[label]
            fields.setdefault(current, []).append(match.group(2).strip())
            continue
        if current == "body_text" and line.strip().startswith(_TERMINATORS):
            current = None
        if current:
            if line.strip():
                fields[current].append(line.strip())
            elif current == "body_text":
                fields[current].append("")
            continue
        # Header area, before any label: the outlet name and the date sit here on their own lines.
        stripped = line.strip()
        if not stripped:
            continue
        if date_guess is None and _DATE_LINE.match(stripped):
            date_guess = _DATE_LINE.match(stripped).group(1)
            continue
        if outlet_guess is None and not stripped.lower().startswith(("copyright", "all rights")):
            outlet_guess = stripped

    def joined(key: str, sep: str = " ") -> str | None:
        vals = fields.get(key)
        if not vals:
            return None
        return sep.join(vals).strip() or None

    body = joined("body_text", "\n")
    language = (joined("language") or "").strip().lower()
    url = joined("url")
    if not url and body:
        found = _URL.search(body)
        url = found.group(0).rstrip(".,);") if found else None

    return {
        "headline": joined("headline"),
        "published_at": date_guess or joined("load_date"),
        "outlet_name": joined("outlet_name") or outlet_guess,
        "byline": joined("byline"),
        "language": _LANGUAGES.get(language, language[:2] or None),
        "country": joined("country"),
        "url": url,
        "body_text": body.strip() if body else None,
    }


def parse_text(text: str) -> list[dict]:
    return [row for row in (parse_block(b) for b in _blocks(text)) if row.get("headline")]


def _looks_tabular(path: Path, encoding: str) -> bool:
    head = path.read_text(encoding=encoding, errors="replace")[:2000]
    if head.lstrip().startswith("{\\rtf") or "<html" in head[:200].lower():
        return False
    if "HEADLINE:" in head:
        return False
    first = head.splitlines()[0] if head else ""
    return first.count(",") >= 3 or first.count("\t") >= 3 or first.count(";") >= 3


def inspect(path: str | Path, encoding: str = "utf-8") -> dict:
    p = Path(path)
    if _looks_tabular(p, encoding):
        info = sniff(p, encoding)
        info["format"] = "csv"
        return info
    rows = parse_text(to_text(p, encoding))
    return {
        "path": str(p), "format": "article_blocks", "articles_found": len(rows),
        "sample_row": rows[0] if rows else None,
        "required_present": bool(rows and rows[0].get("headline") and rows[0].get("published_at")),
    }


def import_file(
    conn: sqlite3.Connection,
    *,
    campaign_ref: str | int,
    path: str | Path,
    mapping: dict[str, str] | None = None,
    default_tier: str = "national_general",
    default_country: str | None = None,
    default_language: str | None = None,
    encoding: str = "utf-8",
    notes: str | None = None,
) -> ImportReport:
    p = Path(path)
    if _looks_tabular(p, encoding):
        resolved = mapping or sniff(p, encoding)["guessed_mapping"]
        rows = read_rows(p, resolved, encoding)
        applied = resolved
    else:
        rows = parse_text(to_text(p, encoding))
        applied = {"format": "nexis_article_blocks", **{v: v for v in sorted(set(LABELS.values()))}}
    if not rows:
        raise ValueError(
            f"No articles found in {p.name}. Export from Nexis with full text (RTF, HTML or TXT) "
            "or as CSV, and check the file is not empty."
        )
    return run_import(
        conn, campaign_ref=campaign_ref, source="nexis", rows=rows, file_path=p,
        mapping=applied, default_tier=default_tier, default_country=default_country,
        default_language=default_language, notes=notes,
    )
