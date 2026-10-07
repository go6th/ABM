"""Monitor registry (SM1): the six monitor kinds, each a registered adapter over the connector
layer. A kind with no available source is ``NotConfigured`` (its tracker row Blocked with a
reason), never a fake success.

GUARANTEE(test_monitor_kinds_are_registered_and_unconfigured_ones_refuse)
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from account_intelligence.platform.ports import NotConfigured

MonitorKind = Literal[
    "build_role_postings",
    "leadership_changes",
    "news_events",
    "tech_detection_changes",
    "facility_expansions",
    "vendor_customer_stories",
]
KINDS: tuple[MonitorKind, ...] = (
    "build_role_postings",
    "leadership_changes",
    "news_events",
    "tech_detection_changes",
    "facility_expansions",
    "vendor_customer_stories",
)
ROW_OF: dict[MonitorKind, str] = {
    "build_role_postings": "job_board_by_lane",
    "leadership_changes": "leadership_changes",
    "news_events": "company_news",
    "tech_detection_changes": "technology_detections",
    "facility_expansions": "facility_expansions",
    "vendor_customer_stories": "vendor_customer_stories",
}


@dataclass(frozen=True)
class Observation:
    """One typed monitor output: a candidate, never a fact (identifiers and quarantined text)."""

    kind: MonitorKind
    item_key: str
    event_type: str
    entity: str
    title: str
    observed_on: datetime
    source_url: str
    source_tier: str
    payload_ref: str


class Monitor(Protocol):
    """A monitor adapter (calls go through ``call_operation``: ledger, cap, rate limits)."""

    kind: MonitorKind

    async def poll(self, account_domain: str, since: datetime | None) -> Sequence[Observation]:
        """Observations since the last poll."""


@dataclass
class UnconfiguredMonitor:
    """The production default until a provider offers the kind."""

    kind: MonitorKind

    async def poll(self, account_domain: str, since: datetime | None) -> Sequence[Observation]:
        """Refuses."""
        raise NotConfigured(f"monitor {self.kind} has no configured source")


Factory = Callable[[MonitorKind], Monitor]


def build(factory: Factory | None = None) -> dict[MonitorKind, Monitor]:
    """One monitor per kind (unconfigured by default)."""
    make: Factory = factory or (lambda k: UnconfiguredMonitor(k))
    return {k: make(k) for k in KINDS}
