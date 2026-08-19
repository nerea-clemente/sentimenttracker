"""The value type every metric returns.

Design principle #1: every number must be traceable. That is enforced here rather than by
convention — a metric returns its value together with the row IDs that produced it, the table
those IDs point at, its denominators, and its plain-language definition. The drill-down view in
the UI is one generic component over `row_ids`, not per-metric bespoke code.

If a value cannot be traced to imported records it must not be displayed. `MetricValue.unavailable`
is how a metric says "I cannot answer this" instead of returning a misleading zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..actor import now_utc
from . import definitions


@dataclass(frozen=True)
class MetricValue:
    key: str
    value: Any
    row_table: str = ""
    row_ids: tuple[int, ...] = ()
    basis: dict[str, Any] = field(default_factory=dict)
    caveats: tuple[str, ...] = ()
    available: bool = True
    unavailable_reason: str = ""
    computed_at: str = field(default_factory=now_utc)

    @property
    def definition(self) -> definitions.Definition:
        return definitions.get(self.key)

    @property
    def label(self) -> str:
        return self.definition.label

    @property
    def unit(self) -> str:
        return self.definition.unit

    def to_dict(self) -> dict[str, Any]:
        d = self.definition
        return {
            "key": self.key,
            "label": d.label,
            "unit": d.unit,
            "value": self.value,
            "available": self.available,
            "unavailable_reason": self.unavailable_reason,
            "row_table": self.row_table,
            "row_ids": list(self.row_ids),
            "row_count": len(self.row_ids),
            "basis": self.basis,
            "caveats": list(self.caveats) + ([d.caveat] if d.caveat else []),
            "definition": {"plain": d.plain, "counts": d.counts, "caveat": d.caveat},
            "computed_at": self.computed_at,
        }


def metric(key: str, value: Any, *, row_table: str = "", row_ids=(), basis=None,
           caveats=()) -> MetricValue:
    definitions.get(key)  # fail loudly if the metric has no written definition
    return MetricValue(
        key=key,
        value=value,
        row_table=row_table,
        row_ids=tuple(int(i) for i in row_ids),
        basis=dict(basis or {}),
        caveats=tuple(caveats),
    )


def unavailable(key: str, reason: str, *, basis=None) -> MetricValue:
    """A metric that cannot be computed. Renders as a stated reason, never as 0."""
    definitions.get(key)
    return MetricValue(
        key=key, value=None, available=False, unavailable_reason=reason, basis=dict(basis or {})
    )


@dataclass
class MetricSet:
    """All metrics for one campaign at one cutoff, with the caveats that must travel with them."""

    campaign_id: int
    campaign_slug: str
    campaign_name: str
    at_day_index: int | None
    metrics: dict[str, MetricValue] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)
    data_quality: dict[str, Any] = field(default_factory=dict)

    def add(self, value: MetricValue) -> None:
        self.metrics[value.key] = value

    def add_all(self, values) -> None:
        for v in values:
            self.add(v)

    def get(self, key: str) -> MetricValue | None:
        return self.metrics.get(key)

    def value(self, key: str):
        m = self.metrics.get(key)
        return m.value if m else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "campaign_slug": self.campaign_slug,
            "campaign_name": self.campaign_name,
            "at_day_index": self.at_day_index,
            "metrics": {k: v.to_dict() for k, v in self.metrics.items()},
            "caveats": self.caveats,
            "data_quality": self.data_quality,
        }
