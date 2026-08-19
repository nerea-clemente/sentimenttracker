"""The metrics engine.

Pure Python over hand-written SQL, callable from the CLI, the API and the tests alike. Every
metric returns a value together with the row IDs that produced it — see `result.MetricValue`.

No metric logic lives anywhere else. The HTTP layer and the frontend consume this module; they do
not reimplement any part of it.
"""

from .compare import (
    METRIC_ORDER,
    PrePublicationError,
    campaign_metrics,
    compare,
    prepublication_view,
)
from .definitions import DEFINITIONS, Definition
from .result import MetricSet, MetricValue

__all__ = [
    "DEFINITIONS",
    "METRIC_ORDER",
    "Definition",
    "MetricSet",
    "MetricValue",
    "PrePublicationError",
    "campaign_metrics",
    "compare",
    "prepublication_view",
]
