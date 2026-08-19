"""RSS, Atom and sitemap parsing.

Used two ways: as an ingestion source for publisher and trade-press feeds, and as the input to the
watch poller that has to catch day zero for a campaign that has not published yet.

Stdlib XML only. Feeds come from outside, so parsing is done with entity resolution disabled to
avoid the usual XML external-entity problems.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from xml.etree import ElementTree

from .base import ImportReport, run_import
from .http import get
from .richtext import html_to_text
from .timeutil_shim import parse_feed_date

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "sitemap": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}


@dataclass
class FeedItem:
    title: str
    link: str
    published_at: str | None = None
    summary: str | None = None
    author: str | None = None
    source_title: str | None = None

    def as_row(self) -> dict:
        return {
            "headline": self.title,
            "published_at": self.published_at,
            "url": self.link,
            "outlet_name": self.source_title,
            "outlet_domain": None,
            "byline": self.author,
            "body_text": self.summary,
        }


def _text(element, path: str) -> str | None:
    if element is None:
        return None
    found = element.find(path, _NS)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


_DOCTYPE = re.compile(r"<!DOCTYPE", re.IGNORECASE)


def _parse_xml(body: str):
    """Parse a feed document.

    Feeds come from outside, so a document declaring a DTD is refused outright. ElementTree does
    not fetch external entities, but an internal entity declaration is how an expansion bomb is
    delivered, and no legitimate RSS, Atom or sitemap feed needs a DOCTYPE.
    """
    if _DOCTYPE.search(body[:4096]):
        raise ValueError("Feed declares a DOCTYPE; refusing to parse untrusted XML with a DTD.")
    return ElementTree.fromstring(body)


def parse_feed(body: str) -> list[FeedItem]:
    """Parse an RSS 2.0, Atom or sitemap document into items."""
    try:
        root = _parse_xml(body)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Not a parseable XML feed: {exc}") from exc

    tag = root.tag.split("}")[-1].lower()
    if tag == "urlset":
        return _parse_sitemap(root)
    if tag == "feed":
        return _parse_atom(root)
    return _parse_rss(root)


def _parse_rss(root) -> list[FeedItem]:
    channel = root.find("channel") or root
    channel_title = _text(channel, "title")
    items = []
    for item in channel.findall(".//item"):
        title = _text(item, "title")
        link = _text(item, "link") or _text(item, "guid")
        if not title or not link:
            continue
        summary = _text(item, "content:encoded") or _text(item, "description")
        items.append(FeedItem(
            title=title,
            link=link,
            published_at=parse_feed_date(_text(item, "pubDate") or _text(item, "dc:date")),
            summary=html_to_text(summary) if summary else None,
            author=_text(item, "dc:creator") or _text(item, "author"),
            source_title=channel_title,
        ))
    return items


def _parse_atom(root) -> list[FeedItem]:
    feed_title = _text(root, "atom:title")
    items = []
    for entry in root.findall("atom:entry", _NS):
        title = _text(entry, "atom:title")
        link_el = entry.find("atom:link[@rel='alternate']", _NS) or entry.find("atom:link", _NS)
        link = link_el.get("href") if link_el is not None else None
        if not title or not link:
            continue
        summary = _text(entry, "atom:content") or _text(entry, "atom:summary")
        items.append(FeedItem(
            title=title,
            link=link,
            published_at=parse_feed_date(
                _text(entry, "atom:published") or _text(entry, "atom:updated")
            ),
            summary=html_to_text(summary) if summary else None,
            author=_text(entry, "atom:author/atom:name"),
            source_title=feed_title,
        ))
    return items


def _parse_sitemap(root) -> list[FeedItem]:
    items = []
    for url in root.findall("sitemap:url", _NS):
        loc = _text(url, "sitemap:loc")
        if not loc:
            continue
        news = url.find("news:news", _NS)
        title = _text(news, "news:title") if news is not None else None
        published = _text(news, "news:publication_date") if news is not None else None
        items.append(FeedItem(
            title=title or loc.rstrip("/").rsplit("/", 1)[-1].replace("-", " "),
            link=loc,
            published_at=parse_feed_date(published or _text(url, "sitemap:lastmod")),
            source_title=_text(news, "news:publication/news:name") if news is not None else None,
        ))
    return items


def fetch(url: str, timeout: int = 30) -> list[FeedItem]:
    return parse_feed(get(url, timeout=timeout))


def import_feed(
    conn: sqlite3.Connection,
    *,
    campaign_ref: str | int,
    url: str,
    outlet_name: str | None = None,
    default_tier: str = "newsletter",
    default_country: str | None = None,
    default_language: str | None = None,
) -> ImportReport:
    items = fetch(url)
    rows = []
    for item in items:
        row = item.as_row()
        if outlet_name:
            row["outlet_name"] = outlet_name
        rows.append(row)
    return run_import(
        conn, campaign_ref=campaign_ref, source="rss", rows=rows,
        mapping={"feed_url": url, "items": len(items)},
        default_tier=default_tier, default_country=default_country,
        default_language=default_language,
        notes=f"RSS/sitemap fetch of {url}; {len(items)} items. Feed summaries are stored as body "
              "text where the feed supplies them, and are usually extracts rather than full text.",
    )


_HTML_LINK = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)


def discover_feeds(page_html: str, base_url: str) -> list[str]:
    """Find feed URLs advertised in a page's <link rel="alternate"> tags."""
    found = []
    for match in re.finditer(r"<link[^>]+>", page_html, re.I):
        tag = match.group(0)
        if "alternate" not in tag.lower():
            continue
        if not re.search(r'type=["\']application/(rss|atom)\+xml', tag, re.I):
            continue
        href = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
        if href:
            link = href.group(1)
            if link.startswith("/"):
                root = base_url.split("//", 1)
                link = f"{root[0]}//{root[1].split('/', 1)[0]}{link}" if len(root) > 1 else link
            found.append(link)
    return found
