"""Media Cloud ingestion — outlet-level coverage.

Needs MEDIACLOUD_API_KEY in .env. Media Cloud returns article metadata and outlet identity but,
under its terms, not full text for most sources; rows imported here therefore usually carry no
body, and the exposure metrics report that gap rather than reading it as zero mentions.
"""

from __future__ import annotations

import sqlite3

from .. import config
from .base import ImportReport, run_import
from .http import FetchError, get_json

SEARCH_API = "https://search.mediacloud.org/api/search/story-list"


def _require_key() -> str:
    key = config.get("MEDIACLOUD_API_KEY").strip()
    if not key:
        raise FetchError(
            "MEDIACLOUD_API_KEY is not set. Add it to .env (see .env.example). Media Cloud "
            "ingestion is optional; the rest of the tool runs without it."
        )
    return key


def search(query: str, start_date: str, end_date: str, collection: str | None = None,
           limit: int = 1000, timeout: int = 60) -> list[dict]:
    """Story list for a query over a date range. Dates are YYYY-MM-DD."""
    key = _require_key()
    stories: list[dict] = []
    pagination_token = None
    while len(stories) < limit:
        payload = get_json(SEARCH_API, {
            "q": query,
            "start_date": start_date,
            "end_date": end_date,
            "collections": collection,
            "platform": "onlinenews-mediacloud",
            "api_key": key,
            "pagination_token": pagination_token,
        }, timeout=timeout)
        batch = payload.get("stories", []) or []
        stories.extend(batch)
        pagination_token = payload.get("pagination_token")
        if not batch or not pagination_token:
            break
    return stories[:limit]


def to_rows(stories: list[dict]) -> list[dict]:
    return [{
        "headline": s.get("title"),
        "published_at": s.get("publish_date"),
        "url": s.get("url"),
        "outlet_name": s.get("media_name") or s.get("media_url"),
        "outlet_domain": s.get("media_url"),
        "country": None,
        "language": s.get("language"),
        "byline": None,
        "body_text": s.get("text") or None,
    } for s in stories]


def import_query(
    conn: sqlite3.Connection,
    *,
    campaign_ref: str | int,
    query: str,
    start_date: str,
    end_date: str,
    collection: str | None = None,
    limit: int = 1000,
    default_tier: str = "blog",
) -> ImportReport:
    stories = search(query, start_date, end_date, collection, limit)
    rows = to_rows(stories)
    with_text = sum(1 for r in rows if r["body_text"])
    note = (
        f"Media Cloud query {query!r} {start_date}..{end_date}; {len(stories)} stories; "
        f"{with_text} with body text. Rows without body text cannot be scanned for mentions."
    )
    return run_import(
        conn, campaign_ref=campaign_ref, source="mediacloud", rows=rows,
        mapping={"api": "mediacloud_story_list", "query": query,
                 "start_date": start_date, "end_date": end_date, "collection": collection},
        default_tier=default_tier, notes=note,
    )
