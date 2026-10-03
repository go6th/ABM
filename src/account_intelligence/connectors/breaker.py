"""Per-provider circuit breaker: closed, open, half-open.

The backstop, not the retry mechanism. It opens after ``failure_threshold``
consecutive retryable failures inside ``window_s``, refuses calls for
``cooldown_s``, then lets ``half_open_probes`` calls through; a probe success closes
it, a probe failure reopens it. State lives in an injected store with an injected
clock, so several workers can share it and tests control time.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Protocol

from account_intelligence.connectors.base import Clock, ProviderCode
from account_intelligence.connectors.errors import CircuitOpen

State = Literal["closed", "open", "half_open"]


@dataclass(frozen=True)
class BreakerState:
    """Persisted breaker state for one provider."""

    state: State = "closed"
    failures: int = 0
    first_failure_at: float = 0.0
    opened_at: float = 0.0
    probes_in_flight: int = 0


@dataclass(frozen=True)
class BreakerConfig:
    """Breaker thresholds: consecutive failures to open and the half-open cooldown."""

    failure_threshold: int = 3  # equal to the node RetryPolicy max_attempts by default
    window_s: float = 60.0
    cooldown_s: float = 30.0
    half_open_probes: int = 1


class BreakerStore(Protocol):
    """Where breaker state lives (in memory per instance today)."""

    async def load(self, key: str) -> BreakerState:
        """The provider's state (closed when never seen)."""
        ...

    async def save(self, key: str, state: BreakerState) -> None:
        """Persist the provider's state."""
        ...


class InMemoryBreakerStore:
    """Per-process breaker state (a backstop; the node RetryPolicy owns retries)."""

    def __init__(self) -> None:
        self._states: dict[str, BreakerState] = {}

    async def load(self, key: str) -> BreakerState:
        """The provider's state (closed when never seen)."""
        return self._states.get(key, BreakerState())

    async def save(self, key: str, state: BreakerState) -> None:
        """Persist the provider's state in memory."""
        self._states[key] = state


class CircuitBreaker:
    """Per-provider circuit breaker: closed, open, half-open with a single probe."""

    def __init__(self, store: BreakerStore, clock: Clock, config: BreakerConfig) -> None:
        self._store = store
        self._clock = clock
        self.config = config

    def _now(self) -> float:
        return self._clock.now().timestamp()

    async def state(self, provider: ProviderCode) -> State:
        """The provider's current state, moving open to half-open after the cooldown."""
        current = await self._store.load(provider)
        if current.state == "open" and self._now() - current.opened_at >= self.config.cooldown_s:
            return "half_open"
        return current.state

    async def before_call(self, provider: ProviderCode) -> None:
        """Raise CircuitOpen while open; admit one probe when half-open."""
        current = await self._store.load(provider)
        now = self._now()
        if current.state == "open":
            if now - current.opened_at < self.config.cooldown_s:
                raise CircuitOpen(provider_code=provider)
            current = replace(current, state="half_open", probes_in_flight=0)
        if current.state == "half_open":
            if current.probes_in_flight >= self.config.half_open_probes:
                raise CircuitOpen(provider_code=provider)
            current = replace(current, probes_in_flight=current.probes_in_flight + 1)
        await self._store.save(provider, current)

    async def record_success(self, provider: ProviderCode) -> None:
        """Close the breaker after a successful call."""
        await self._store.save(provider, BreakerState())

    async def record_neutral(self, provider: ProviderCode) -> None:
        """A non-retryable outcome: the provider answered, so release a probe slot."""
        current = await self._store.load(provider)
        if current.state == "half_open":
            await self._store.save(provider, BreakerState())

    async def record_failure(self, provider: ProviderCode) -> None:
        """Count a retryable failure; open the breaker at the threshold."""
        current = await self._store.load(provider)
        now = self._now()
        if current.state == "half_open":
            await self._store.save(provider, BreakerState(state="open", opened_at=now))
            return
        if current.failures == 0 or now - current.first_failure_at > self.config.window_s:
            current = BreakerState(failures=1, first_failure_at=now)
        else:
            current = replace(current, failures=current.failures + 1)
        if current.failures >= self.config.failure_threshold:
            current = BreakerState(state="open", opened_at=now)
        await self._store.save(provider, current)
