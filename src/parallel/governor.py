"""Concurrency governor: per-provider leases across all runs and workers (D-43, P4, P5, P6).

**Decision rule** (``choose``, pure, the same rule as the SQL function ``governor_acquire`` in
migration 0017; a parity test runs both on generated scenarios):

1. expired leases are reclaimed (a dead holder's slot returns after its TTL) and waiters not seen
   within the waiter TTL are dropped;
2. no grant while the provider's active leases are at its current limit (P4);
3. a run may hold at most ``max(1, limit * run_share%)`` and a tenant ``max(1, limit *
   tenant_share%)`` of the provider's slots (P5);
4. among eligible live waiters the slot goes to the one whose tenant holds fewest slots, then the
   highest aged priority (priority plus one per ``aging_step_s`` waited: aging bounds the wait),
   then the oldest, then the holder id. A requester is granted only if it is that waiter.

**AIMD** (``aimd``, pure; P6): a rate-limit signal halves the limit (floor 1); a clean response
grows it by one when a full window has passed since the last change and the last rate-limit
signal, never above the ceiling; at most one change per window. A decrease applies to new grants;
calls already in flight finish (so ``active <= ceiling`` always holds, and no lease is ever granted
while ``active >= limit``).

**Shells.** ``InMemoryGovernor`` (one process: the simulation and unit tests) and
``PostgresGovernor`` (production: SECURITY DEFINER functions that count across tenants and return
only a lease id or NULL, so no tenant's rows are exposed). Both implement the connector's
``ProviderGovernor`` port: ``slot`` waits a bounded time with jitter, heartbeats while held and
releases in ``finally``; it never retries a provider call.

GUARANTEE(test_governor_never_grants_at_or_above_the_limit): property test (P4).
GUARANTEE(test_governor_reclaims_a_dead_holders_lease): TTL reclaim (P4).
GUARANTEE(test_governor_fairness_bounds_a_floods_advantage): property test (P5).
GUARANTEE(test_aimd_halves_on_rate_limit_and_grows_by_one_per_clean_window): P6.
GUARANTEE(test_postgres_and_memory_governors_agree): parity of the SQL and Python rules.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import uuid
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.connectors.base import CallContext
from account_intelligence.connectors.governance import Signal, SlotUnavailable
from parallel.config import ProviderSettings

TxnFactory = Callable[[UUID], contextlib.AbstractAsyncContextManager[AsyncSession]]


class GovClock(Protocol):
    """Wall time for lease stamps and a sleep for bounded waits (injected; virtual in tests)."""

    def now(self) -> datetime:
        """Current time (timezone aware)."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Wait."""
        ...


@dataclass(frozen=True)
class Lease:
    """An active or released lease."""

    lease_id: str
    tenant: str
    run: str
    holder: str
    expires_at: datetime


@dataclass(frozen=True)
class Waiter:
    """A requester waiting for a slot."""

    tenant: str
    run: str
    holder: str
    priority: int
    since: datetime
    seen_at: datetime


@dataclass(frozen=True)
class Shares:
    """Fairness knobs (percentages and the aging step)."""

    run_pct: int
    tenant_pct: int
    aging_step_s: float


def share(limit: int, pct: int) -> int:
    """At least one slot; otherwise the integer share of the limit."""
    return max(1, limit * pct // 100)


def choose(
    limit: int,
    active: Iterable[Lease],
    waiters: Iterable[Waiter],
    shares: Shares,
    now: datetime,
) -> str | None:
    """The holder the next slot goes to, or None (see the module docstring, rules 2 to 4)."""
    leases = list(active)
    if len(leases) >= limit:
        return None
    by_run: dict[str, int] = {}
    by_tenant: dict[str, int] = {}
    for lease in leases:
        by_run[lease.run] = by_run.get(lease.run, 0) + 1
        by_tenant[lease.tenant] = by_tenant.get(lease.tenant, 0) + 1
    run_cap, tenant_cap = share(limit, shares.run_pct), share(limit, shares.tenant_pct)
    eligible = [
        w
        for w in waiters
        if by_run.get(w.run, 0) < run_cap and by_tenant.get(w.tenant, 0) < tenant_cap
    ]
    if not eligible:
        return None

    def aged(w: Waiter) -> int:
        waited = max(0.0, (now - w.since).total_seconds())
        return w.priority + int(waited // shares.aging_step_s)

    best = min(eligible, key=lambda w: (by_tenant.get(w.tenant, 0), -aged(w), w.since, w.holder))
    return best.holder


@dataclass(frozen=True)
class LimitState:
    """The adaptive limit of one provider."""

    limit: int
    ceiling: int
    floor: int = 1
    last_change_at: datetime | None = None
    last_rate_limited_at: datetime | None = None


LimitEvent = Literal["decrease", "increase"]


def aimd(
    state: LimitState, kind: Signal, now: datetime, window_s: float
) -> tuple[LimitState, LimitEvent | None]:
    """One AIMD step (see the module docstring)."""
    window = timedelta(seconds=window_s)
    changed_recently = state.last_change_at is not None and now - state.last_change_at < window
    if kind == "rate_limited":
        state = replace(state, last_rate_limited_at=now)
        new = max(state.floor, state.limit // 2)
        if changed_recently or new >= state.limit:
            return state, None
        return replace(state, limit=new, last_change_at=now), "decrease"
    clean_since = state.last_rate_limited_at is None or now - state.last_rate_limited_at >= window
    if changed_recently or not clean_since or state.limit >= state.ceiling:
        return state, None
    if state.last_change_at is None and state.last_rate_limited_at is None:
        # no history yet: the first clean window starts now (grow only after a full window)
        return replace(state, last_change_at=now), None
    return replace(state, limit=state.limit + 1, last_change_at=now), "increase"


# --------------------------------------------------------------------------- shells


class _SlotMixin:
    """The bounded wait, heartbeat and release shared by both shells."""

    settings: ProviderSettings
    clock: GovClock
    rng: random.Random

    async def _try(
        self, ctx: CallContext, provider: str, holder: str, priority: int
    ) -> str | None:  # pragma: no cover - abstract
        raise NotImplementedError

    async def _heartbeat(self, ctx: CallContext, lease_id: str) -> bool:  # pragma: no cover
        raise NotImplementedError

    async def _release(self, ctx: CallContext, lease_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    @contextlib.asynccontextmanager
    async def slot(
        self, ctx: CallContext, provider_code: str, priority: int
    ) -> AsyncIterator[float]:
        """Hold one slot (bounded wait with jitter, then ``SlotUnavailable``)."""
        holder = f"{ctx.run_id}:{uuid.uuid4().hex}"
        waited = 0.0
        # LOOP(lease wait): cap=lease_wait_s of polls every lease_poll_s plus jitter; exit=granted,
        # SlotUnavailable (the unit is re-queued with its age preserved; never a provider retry)
        while True:
            lease_id = await self._try(ctx, provider_code, holder, priority)
            if lease_id is not None:
                break
            if waited >= self.settings.lease_wait_s:
                raise SlotUnavailable(provider_code, waited)
            pause = self.settings.lease_poll_s + self.rng.uniform(0, self.settings.lease_jitter_s)
            await self.clock.sleep(pause)
            waited += pause
        beat = asyncio.create_task(self._beat(ctx, lease_id))
        try:
            yield waited
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
            await self._release(ctx, lease_id)

    async def _beat(self, ctx: CallContext, lease_id: str) -> None:
        # LOOP(lease heartbeat): cap=lifetime of the held slot (cancelled on release); exit=release,
        # lost lease (the slot expires at its TTL and is reclaimed)
        while True:
            await self.clock.sleep(self.settings.heartbeat_s)
            if not await self._heartbeat(ctx, lease_id):
                return


@dataclass
class _ProviderState:
    limit: LimitState
    leases: dict[str, Lease] = field(default_factory=dict)
    waiters: dict[str, Waiter] = field(default_factory=dict)
    grants_at_limit: int = 0  # invariant monitor: must stay 0


class InMemoryGovernor(_SlotMixin):
    """One-process governor with the exact decision rule (simulation and unit tests)."""

    def __init__(
        self,
        settings: ProviderSettings,
        shares: Shares,
        clock: GovClock,
        rng: random.Random | None = None,
        tenant_of: Callable[[CallContext], str] | None = None,
    ) -> None:
        self.settings = settings
        self.shares = shares
        self.clock = clock
        self.rng = rng or random.Random(0)  # noqa: S311 - jitter only
        self._tenant_of = tenant_of or (lambda ctx: str(ctx.tenant_id))
        self.providers: dict[str, _ProviderState] = {}
        self.events: list[tuple[str, LimitEvent, int, int, datetime]] = []

    def state(self, provider: str) -> _ProviderState:
        """The provider's state (created at the configured floor and ceiling)."""
        if provider not in self.providers:
            ceiling = self.settings.ceiling(provider)
            self.providers[provider] = _ProviderState(
                LimitState(self.settings.floor, ceiling, self.settings.floor)
            )
        return self.providers[provider]

    def active(self, provider: str) -> int:
        """Active (unexpired, unreleased) leases now."""
        now = self.clock.now()
        return sum(1 for x in self.state(provider).leases.values() if x.expires_at > now)

    def acquire_now(
        self, provider: str, tenant: str, run: str, holder: str, priority: int
    ) -> str | None:
        """One acquisition attempt (synchronous; the rule of the module docstring)."""
        st = self.state(provider)
        now = self.clock.now()
        ttl = timedelta(seconds=self.settings.lease_ttl_s)
        st.leases = {k: v for k, v in st.leases.items() if v.expires_at > now}
        live = timedelta(seconds=self.settings.waiter_ttl_s)
        st.waiters = {k: w for k, w in st.waiters.items() if now - w.seen_at < live}
        old = st.waiters.get(holder)
        st.waiters[holder] = Waiter(tenant, run, holder, priority, old.since if old else now, now)
        chosen = choose(st.limit.limit, st.leases.values(), st.waiters.values(), self.shares, now)
        if chosen != holder:
            return None
        if len(st.leases) >= st.limit.limit:  # pragma: no cover - choose() already refuses
            st.grants_at_limit += 1
        lease_id = uuid.uuid4().hex
        st.leases[lease_id] = Lease(lease_id, tenant, run, holder, now + ttl)
        del st.waiters[holder]
        return lease_id

    async def _try(self, ctx: CallContext, provider: str, holder: str, priority: int) -> str | None:
        return self.acquire_now(provider, self._tenant_of(ctx), str(ctx.run_id), holder, priority)

    async def _heartbeat(self, ctx: CallContext, lease_id: str) -> bool:
        now = self.clock.now()
        for st in self.providers.values():
            lease = st.leases.get(lease_id)
            if lease is not None and lease.expires_at > now:
                ttl = timedelta(seconds=self.settings.lease_ttl_s)
                st.leases[lease_id] = replace(lease, expires_at=now + ttl)
                return True
        return False

    async def _release(self, ctx: CallContext, lease_id: str) -> None:
        for st in self.providers.values():
            st.leases.pop(lease_id, None)

    async def signal(self, ctx: CallContext, provider_code: str, kind: Signal) -> None:
        """Apply one AIMD step and record a change."""
        st = self.state(provider_code)
        before = st.limit.limit
        st.limit, event = aimd(st.limit, kind, self.clock.now(), self.settings.aimd_window_s)
        if event is not None:
            self.events.append((provider_code, event, before, st.limit.limit, self.clock.now()))


class PostgresGovernor(_SlotMixin):
    """Production governor: leases in Postgres through the migration 0017 functions. Each call is
    its own short transaction under the run's tenant context (never held across a provider call)."""

    def __init__(
        self,
        txn: TxnFactory,
        settings: ProviderSettings,
        shares: Shares,
        clock: GovClock,
        rng: random.Random | None = None,
    ) -> None:
        self.txn = txn
        self.settings = settings
        self.shares = shares
        self.clock = clock
        self.rng = rng or random.Random()  # noqa: S311 - jitter only
        self._configured: set[str] = set()  # providers whose bounds this process applied

    async def configure(self, tenant_id: UUID, providers: Iterable[str]) -> None:
        """Make sure each provider has a limit row at the configured floor and ceiling."""
        async with self.txn(tenant_id) as s:
            for code in sorted(set(providers)):
                await s.execute(
                    text("SELECT governor_configure(:p, :c, :f, :now)"),
                    {
                        "p": code,
                        "c": self.settings.ceiling(code),
                        "f": self.settings.floor,
                        "now": self.clock.now(),
                    },
                )

    async def _try(self, ctx: CallContext, provider: str, holder: str, priority: int) -> str | None:
        async with self.txn(ctx.tenant_id) as s:
            if provider not in self._configured:
                # apply the configured bounds once per process (a changed ceiling takes effect)
                await s.execute(
                    text("SELECT governor_configure(:p, :c, :f, :now)"),
                    {
                        "p": provider,
                        "c": self.settings.ceiling(provider),
                        "f": self.settings.floor,
                        "now": self.clock.now(),
                    },
                )
                self._configured.add(provider)
            value = (
                await s.execute(
                    text(
                        "SELECT governor_acquire(:p, :r, :h, :prio, :now, :ttl, :wttl, :rs, :ts,"
                        " :age, :ceil, :floor)"
                    ),
                    {
                        "p": provider,
                        "r": ctx.run_id,
                        "h": holder,
                        "prio": priority,
                        "now": self.clock.now(),
                        "ttl": self.settings.lease_ttl_s,
                        "wttl": self.settings.waiter_ttl_s,
                        "rs": self.shares.run_pct,
                        "ts": self.shares.tenant_pct,
                        "age": self.shares.aging_step_s,
                        "ceil": self.settings.ceiling(provider),
                        "floor": self.settings.floor,
                    },
                )
            ).scalar_one_or_none()
        return None if value is None else str(value)

    async def _heartbeat(self, ctx: CallContext, lease_id: str) -> bool:
        async with self.txn(ctx.tenant_id) as s:
            ok: object = (
                await s.execute(
                    text("SELECT governor_heartbeat(:l, :now, :ttl)"),
                    {"l": lease_id, "now": self.clock.now(), "ttl": self.settings.lease_ttl_s},
                )
            ).scalar_one()
        return bool(ok)

    async def _release(self, ctx: CallContext, lease_id: str) -> None:
        async with self.txn(ctx.tenant_id) as s:
            await s.execute(
                text("SELECT governor_release(:l, :now)"), {"l": lease_id, "now": self.clock.now()}
            )

    async def signal(self, ctx: CallContext, provider_code: str, kind: Signal) -> None:
        """Apply one AIMD step (``governor_signal``; at most one change per window)."""
        async with self.txn(ctx.tenant_id) as s:
            await s.execute(
                text("SELECT governor_signal(:p, :k, :now, :w)"),
                {
                    "p": provider_code,
                    "k": kind,
                    "now": self.clock.now(),
                    "w": self.settings.aimd_window_s,
                },
            )
