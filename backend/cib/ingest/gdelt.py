"""GDELT DOC 2.0 ingestion.

GDELT gives cross-country volume and outlet counts for a keyword query. It needs no API key.

What it is good for: seeing that a story has broken internationally, and how widely, within
minutes. What it is not good for: full text (GDELT returns none) or authoritative outlet identity.
Articles imported from here therefore have no body text, which means they cannot be scanned for
entity mentions — the exposure metrics report that gap rather than assuming zero mentions.
"""

from __future__ import annotations

import sqlite3

from .base import ImportReport, run_import
from .http import get_json

DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"

# GDELT's own language names, mapped to ISO 639-1 where unambiguous.
_LANGUAGES = {
    "English": "en", "Danish": "da", "German": "de", "French": "fr", "Spanish": "es",
    "Portuguese": "pt", "Norwegian": "no", "Swedish": "sv", "Dutch": "nl", "Italian": "it",
    "Chinese": "zh", "Japanese": "ja", "Russian": "ru", "Arabic": "ar",
}


def search(query: str, start: str | None = None, end: str | None = None,
           max_records: int = 250, timeout: int = 30) -> list[dict]:
    """Query the DOC 2.0 article list. Dates are YYYYMMDDHHMMSS."""
    payload = get_json(DOC_API, {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": min(int(max_records), 250),
        "startdatetime": start,
        "enddatetime": end,
        "sort": "datedesc",
    }, timeout=timeout)
    return payload.get("articles", []) or []


def to_rows(articles: list[dict]) -> list[dict]:
    """Map GDELT article records onto canonical import rows.

    `seendate` is GDELT's timestamp for when it saw the article, not necessarily the outlet's own
    publication time. That distinction is recorded in the import notes rather than papered over.
    """
    rows = []
    for a in articles:
        seen = str(a.get("seendate") or "")
        published = (
            f"{seen[0:4]}-{seen[4:6]}-{seen[6:8]}T{seen[9:11]}:{seen[11:13]}:{seen[13:15]}"
            if len(seen) >= 15 else (f"{seen[0:4]}-{seen[4:6]}-{seen[6:8]}" if len(seen) >= 8
                                     else None)
        )
        rows.append({
            "headline": a.get("title"),
            "published_at": published,
            "url": a.get("url"),
            "outlet_name": a.get("domain"),
            "outlet_domain": a.get("domain"),
            "country": a.get("sourcecountry") or None,
            "language": _LANGUAGES.get(a.get("language")),
            "byline": None,
            "body_text": None,   # GDELT returns no full text. Never fabricate one.
        })
    return rows


def import_query(
    conn: sqlite3.Connection,
    *,
    campaign_ref: str | int,
    query: str,
    start: str | None = None,
    end: str | None = None,
    max_records: int = 250,
    default_tier: str = "blog",
) -> ImportReport:
    articles = search(query, start, end, max_records)
    rows = to_rows(articles)
    note = (
        f"GDELT DOC 2.0 query {query!r}"
        + (f" from {start}" if start else "")
        + (f" to {end}" if end else "")
        + f"; {len(articles)} records returned (API cap is 250 per call). "
        "GDELT supplies no article body, so these rows cannot be scanned for entity mentions. "
        "Timestamps are GDELT's crawl time, which can lag the outlet's own publication time."
    )
    return run_import(
        conn, campaign_ref=campaign_ref, source="gdelt", rows=rows,
        mapping={"api": "gdelt_doc_2.0", "query": query, "start": start, "end": end},
        default_tier=default_tier, notes=note,
    )


def volume_timeline(query: str, start: str | None = None, end: str | None = None,
                    timeout: int = 30) -> list[dict]:
    """GDELT's own volume timeline for a query. Context only — never imported as articles."""
    payload = get_json(DOC_API, {
        "query": query, "mode": "TimelineVolInfo", "format": "json",
        "startdatetime": start, "enddatetime": end,
    }, timeout=timeout)
    series = payload.get("timeline") or []
    return series[0].get("data", []) if series else []
