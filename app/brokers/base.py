"""Broker connector interface and the per-run session that enforces pull-once and spend tracking.

Roadmap s11.2 connector requirements covered here: typed request and response, caching keyed on
the request, raw response stored with a neutral name, cost recorded per call, a recorded-response
test double. Retry, rate limits and the contract check job are Phase 1 work items.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

JSONValue = Any


class BrokerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    broker: str
    operation: str
    params: dict[str, JSONValue] = Field(default_factory=dict)

    def cache_key(self) -> str:
        """Stable key for the request: same broker, operation and params give the same key."""
        canonical = json.dumps(
            {"broker": self.broker, "operation": self.operation, "params": self.params},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()[:24]

    def neutral_name(self) -> str:
        """Raw file name with no broker or account name in it (PRD s4.4, roadmap s7)."""
        return f"raw_{self.cache_key()}.json"


class BrokerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request_key: str
    payload: dict[str, JSONValue]
    cost_credits: float = Field(ge=0)


class Connector(Protocol):
    broker: str

    def call(self, request: BrokerRequest) -> BrokerResponse: ...


class BrokerError(RuntimeError):
    pass


@dataclass(frozen=True)
class CallRecord:
    request_key: str
    broker: str
    operation: str
    cost_credits: float
    served_from_cache: bool


@dataclass
class BrokerSession:
    """One run's view of the brokers.

    Every call goes through here, so a request already pulled in this run is served from the
    stored raw response and costs nothing (roadmap s3 principle 2, "a source is never paid for
    twice in a run").
    """

    connectors: dict[str, Connector]
    raw_store: dict[str, dict[str, JSONValue]] = field(default_factory=dict)
    calls: list[CallRecord] = field(default_factory=list)
    _responses: dict[str, BrokerResponse] = field(default_factory=dict)

    def call(self, request: BrokerRequest) -> BrokerResponse:
        key = request.cache_key()
        cached = self._responses.get(key)
        if cached is not None:
            self.calls.append(CallRecord(key, request.broker, request.operation, 0.0, True))
            return cached
        connector = self.connectors.get(request.broker)
        if connector is None:
            raise BrokerError(f"no connector registered for {request.broker!r}")
        response = connector.call(request)
        if response.request_key != key:
            raise BrokerError("connector returned a response for a different request")
        self._responses[key] = response
        self.raw_store[request.neutral_name()] = response.payload
        self.calls.append(
            CallRecord(key, request.broker, request.operation, response.cost_credits, False)
        )
        return response

    def spend_by_broker(self) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for c in self.calls:
            totals[c.broker] += c.cost_credits
        return dict(totals)

    def total_spend(self) -> float:
        return sum(c.cost_credits for c in self.calls)

    def paid_pulls(self) -> int:
        return sum(1 for c in self.calls if not c.served_from_cache)

    def duplicate_paid_pulls(self) -> list[str]:
        """Keys pulled from a broker more than once; must always be empty."""
        seen: dict[str, int] = defaultdict(int)
        for c in self.calls:
            if not c.served_from_cache:
                seen[c.request_key] += 1
        return sorted(k for k, n in seen.items() if n > 1)
