"""Row types.

Plain dataclasses over sqlite3.Row — no ORM, no lazy loading, no hidden queries. Constructed with
`from_row`, which ignores columns the dataclass does not declare so migrations can add columns
without breaking readers.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, fields
from typing import Any, TypeVar

T = TypeVar("T")

CAMPAIGN_TYPES = ("journalism", "ngo_report", "coalition", "regulatory")
CAMPAIGN_STATUSES = ("pre_publication", "live", "decaying", "archived")
OUTLET_TIERS = (
    "national_general", "national_business", "trade", "regional",
    "broadcast", "wire", "aggregator", "ngo", "newsletter", "blog",
)
ENTITY_TYPES = (
    "own_company", "competitor", "ngo", "certifier", "customer", "supplier", "regulator",
)
MENTION_ROLES = (
    "subject", "named_supplier", "named_buyer", "quoted_response", "passing_reference",
)
ESCALATION_TYPES = (
    "legal_petition", "regulatory_action", "customs_measure", "retailer_statement",
    "buyer_statement", "parliamentary_question", "certifier_response", "company_response", "other",
)
INBOUND_CHANNELS = ("journalist", "customer", "tender", "investor", "employee", "other")
IMPORT_SOURCES = ("infomedia", "factiva", "nexis", "gdelt", "mediacloud", "rss", "manual")
WATCH_RULE_TYPES = ("keyword", "phrase", "byline", "domain", "rss", "sitemap", "gdelt_query")

# Weights for the exposure depth score. Always reported with their components, never as a bare
# scalar — see metrics/exposure.py and README "Depth score".
ROLE_WEIGHTS = {
    "subject": 5,
    "named_supplier": 3,
    "named_buyer": 3,
    "quoted_response": 2,
    "passing_reference": 1,
}

# Anchors for escalations.severity. Written down because a severity-weighted total is only
# defensible if the scale is.
SEVERITY_ANCHORS = {
    1: "Statement of concern",
    2: "Formal query or parliamentary question",
    3: "Buyer, retailer or certifier action",
    4: "Regulatory investigation opened",
    5: "Binding regulatory or customs measure, or litigation filed",
}


def _build(cls: type[T], row: sqlite3.Row | dict | None) -> T | None:
    if row is None:
        return None
    data = dict(row)
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})


def _json_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


@dataclass
class Campaign:
    id: int
    name: str
    slug: str
    publisher_org: str
    campaign_type: str
    status: str
    published_at: str | None = None
    # How precisely published_at is known: 'day', 'month' or 'year'. Anything but 'day' means the
    # day-aligned figures carry an error bar and every metric set says so.
    published_at_precision: str = "day"
    first_signal_at: str | None = None
    timezone: str = "UTC"
    themes: str = "[]"
    notes: str | None = None
    created_at: str = ""
    created_by: str = ""

    @property
    def theme_list(self) -> list[str]:
        return _json_list(self.themes)

    @property
    def is_pre_publication(self) -> bool:
        return self.published_at is None

    @property
    def date_is_exact(self) -> bool:
        return self.published_at is None or self.published_at_precision == "day"

    @classmethod
    def from_row(cls, row) -> Campaign | None:
        return _build(cls, row)


@dataclass
class Outlet:
    id: int
    name: str
    tier: str
    domain: str | None = None
    country: str | None = None
    language: str | None = None
    reach_value: int | None = None
    reach_source: str | None = None
    reach_is_estimated: int = 0
    reach_estimation_method: str | None = None
    reach_as_of: str | None = None
    is_known_syndication_partner: int = 0
    notes: str | None = None
    created_at: str = ""
    created_by: str = ""

    @classmethod
    def from_row(cls, row) -> Outlet | None:
        return _build(cls, row)


@dataclass
class Article:
    id: int
    campaign_id: int
    outlet_id: int
    headline: str
    published_at: str
    import_id: int
    url: str | None = None
    day_index: int | None = None
    language: str | None = None
    country: str | None = None
    byline: str | None = None
    body_text: str | None = None
    body_hash: str | None = None
    word_count: int | None = None
    cluster_id: int | None = None
    is_original: int = 1
    row_fingerprint: str | None = None
    retrieved_at: str | None = None
    created_at: str = ""
    created_by: str = ""

    @classmethod
    def from_row(cls, row) -> Article | None:
        return _build(cls, row)


@dataclass
class Entity:
    id: int
    name: str
    type: str
    aliases: str = "[]"
    notes: str | None = None
    created_at: str = ""
    created_by: str = ""

    @property
    def alias_list(self) -> list[str]:
        return _json_list(self.aliases)

    @property
    def surface_forms(self) -> list[str]:
        return [self.name, *self.alias_list]

    @classmethod
    def from_row(cls, row) -> Entity | None:
        return _build(cls, row)


@dataclass
class Mention:
    id: int
    article_id: int
    entity_id: int
    role: str
    evidence_sentence: str
    char_offset: int | None = None
    confidence: float | None = None
    classified_by: str = "rule"
    created_at: str = ""
    created_by: str = ""

    @classmethod
    def from_row(cls, row) -> Mention | None:
        return _build(cls, row)


@dataclass
class Escalation:
    id: int
    campaign_id: int
    occurred_at: str
    escalation_type: str
    actor_name: str
    description: str
    source_url: str
    severity: int
    actor_type: str | None = None
    verified_by: str | None = None
    verified_at: str | None = None
    created_at: str = ""
    created_by: str = ""

    @classmethod
    def from_row(cls, row) -> Escalation | None:
        return _build(cls, row)


@dataclass
class InboundSignal:
    id: int
    occurred_at: str
    channel: str
    summary: str
    logged_by: str
    campaign_id: int | None = None
    source_ref: str | None = None
    created_at: str = ""
    created_by: str = ""

    @classmethod
    def from_row(cls, row) -> InboundSignal | None:
        return _build(cls, row)


@dataclass
class WatchRule:
    id: int
    name: str
    rule_type: str
    pattern: str
    campaign_id: int | None = None
    source_url: str | None = None
    enabled: int = 1
    promotes_to_live: int = 0
    last_polled_at: str | None = None
    last_error: str | None = None
    notes: str | None = None
    created_at: str = ""
    created_by: str = ""

    @classmethod
    def from_row(cls, row) -> WatchRule | None:
        return _build(cls, row)
