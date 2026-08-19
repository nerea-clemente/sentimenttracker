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

# GDELT reports `sourcecountry` as a full country name ("Denmark"), while every other source in
# this tool stores ISO 3166-1 alpha-2 ("DK"). Passing the name straight through would make the
# country count treat "Denmark" and "DK" as two different countries and silently inflate a
# headline metric, so names are mapped here. Anything unmapped becomes NULL and is reported as a
# coverage gap by the data-quality figures — a missing country is recoverable, a wrong one is not.
_COUNTRIES = {
    # Nordics and the rest of Europe
    "Denmark": "DK", "Norway": "NO", "Sweden": "SE", "Finland": "FI", "Iceland": "IS",
    "United Kingdom": "GB", "Ireland": "IE", "Netherlands": "NL", "Belgium": "BE",
    "Germany": "DE", "France": "FR", "Spain": "ES", "Portugal": "PT", "Italy": "IT",
    "Poland": "PL", "Switzerland": "CH", "Austria": "AT", "Greece": "GR", "Russia": "RU",
    "Ukraine": "UA", "Turkey": "TR", "Estonia": "EE", "Latvia": "LV", "Lithuania": "LT",
    # Americas
    "United States": "US", "Canada": "CA", "Mexico": "MX", "Brazil": "BR", "Chile": "CL",
    "Peru": "PE", "Argentina": "AR", "Ecuador": "EC", "Colombia": "CO",
    # West Africa and the wider fishmeal supply chain
    "Mauritania": "MR", "Senegal": "SN", "Gambia": "GM", "Morocco": "MA", "Ghana": "GH",
    "Nigeria": "NG", "Ivory Coast": "CI", "Guinea": "GN", "Guinea-Bissau": "GW",
    "Sierra Leone": "SL", "Liberia": "LR", "Namibia": "NA", "South Africa": "ZA",
    "Angola": "AO", "Egypt": "EG", "Kenya": "KE", "Tanzania": "TZ",
    # Asia-Pacific
    "China": "CN", "Japan": "JP", "South Korea": "KR", "India": "IN", "Vietnam": "VN",
    "Thailand": "TH", "Indonesia": "ID", "Philippines": "PH", "Malaysia": "MY",
    "Bangladesh": "BD", "Australia": "AU", "New Zealand": "NZ", "Singapore": "SG",
}


def country_code(name: str | None) -> str | None:
    """GDELT country name to ISO 3166-1 alpha-2. None when unmapped, never a guess."""
    if not name:
        return None
    cleaned = name.strip()
    # Tolerate a value that is already an ISO code, in case the API's shape changes.
    if len(cleaned) == 2 and cleaned.isalpha() and cleaned.isupper():
        return cleaned
    return _COUNTRIES.get(cleaned)


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
            "country": country_code(a.get("sourcecountry")),
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
        "Timestamps are GDELT's crawl time, which can lag the outlet's own publication time. "
        "Country is mapped from GDELT's country names to ISO alpha-2; unmapped ones are left "
        "blank and show up in the data-quality gap rather than as a wrong country."
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
