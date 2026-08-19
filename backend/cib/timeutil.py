"""Date handling for day-index alignment.

`day_index` is what makes campaigns comparable: a campaign that ran in 2019 and one running today
must sit on the same x-axis. It is a whole-day offset from the campaign's publication date,
measured on calendar dates in the campaign's own timezone.

Two deliberate choices:
  * Day 0 is the publication date itself, not the day after.
  * day_index may be negative. Embargo breaks and pre-publication leaks are real coverage and
    must not be silently dropped or clamped to zero.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_FORMATS = (
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m/%d/%Y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%b %d, %Y",
)


def parse_timestamp(value: str | datetime | date | None) -> datetime | None:
    """Parse a timestamp from any of the shapes the archive exports use. None if unparseable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+0000"
    # Normalise "+01:00" to "+0100" for %z on older-style inputs.
    if len(raw) > 6 and raw[-3] == ":" and raw[-6] in "+-":
        raw = raw[:-3] + raw[-2:]
    for fmt in _FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def zone(name: str | None):
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def local_date(value: str | datetime | None, tz_name: str) -> date | None:
    """The calendar date of a timestamp, as seen in the campaign's timezone."""
    dt = parse_timestamp(value)
    if dt is None:
        return None
    tz = zone(tz_name)
    if dt.tzinfo is None:
        # Naive timestamps are read as already being local to the campaign's zone. Archive
        # exports rarely carry an offset, and inventing UTC for them would shift day boundaries.
        return dt.date()
    return dt.astimezone(tz).date()


def day_index(published_at: str | datetime | None, article_at: str | datetime | None,
              tz_name: str = "UTC") -> int | None:
    """Whole days from campaign publication to article publication. Day 0 is publication day."""
    base = local_date(published_at, tz_name)
    when = local_date(article_at, tz_name)
    if base is None or when is None:
        return None
    return (when - base).days


def date_for_day_index(published_at: str | datetime | None, index: int,
                       tz_name: str = "UTC") -> date | None:
    base = local_date(published_at, tz_name)
    if base is None:
        return None
    return base + timedelta(days=index)


def iso_date(d: date) -> str:
    return d.isoformat()
