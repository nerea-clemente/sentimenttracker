"""RFC 822 / RFC 3339 date parsing for feeds.

Feeds use email-style dates that the archive-export parser in cib.timeutil does not cover, so
they are handled here and normalised to ISO before joining the same pipeline.
"""

from __future__ import annotations

from email.utils import parsedate_to_datetime

from ..timeutil import parse_timestamp


def parse_feed_date(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        pass
    parsed = parse_timestamp(raw)
    return parsed.isoformat() if parsed else None
