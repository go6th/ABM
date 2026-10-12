"""Ledgered call v2: per-run lease, insert-only intent log, pull-once replay.

Guarantees (ADR-0006; proven by tests/connectors/test_ledger_*.py):

* **Pull once.** A key ``(run_id, args_hash)`` with a ``tool_calls`` row is replayed
  from storage; the provider is not called again.
* **One worker per run.** Every claim upserts the run lease in the same transaction;
  a different live owner gets ``LeaseUnavailable``. The lease row lock serialises all
  ledger transactions of a run.
* **Identical concurrent calls pay once.** The first claim writes a ``started`` event
  before the provider is called; an identical claim by the same owner sees it and
  waits for the result (bounded), then replays.
* **At most one repeat per crash.** A ``started`` attempt left by a different owner or
  lease epoch is stale: it is closed as ``abandoned`` (its reservation stays charged)
  and the call is re-issued once with ``possible_duplicate = true``, unless the
  attempt recorded a provider job id, in which case the job is polled instead.
* **Spend equals the ledger.** The budget reservation is written in the same
  transaction as the ``started`` event and trued up in the same transaction as the
  terminal event, so ``account_runs.spent_usd`` equals ``ledger_sum``.

Not guaranteed: exactly-once side effects at the provider. A crash between the
provider's response and the ``done`` write repeats that one call (flagged).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.connectors import budget as budget_mod
from account_intelligence.connectors.base import CallContext, Clock
from account_intelligence.connectors.errors import (
    BudgetExceeded,
    CapRefused,
    LeaseLost,
    LeaseUnavailable,
)

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


def canonical_json(value: Any) -> str:
    """Canonical JSON (sorted keys, fixed separators) for stable hashing."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def args_hash(provider_code: str, operation: str, args: Any) -> str:
    """sha256 of canonical JSON ``[provider_code, operation, args]`` (sorted keys)."""
    return hashlib.sha256(
        canonical_json([provider_code, operation, args]).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class CallKey:
    """The ledger identity of one paid call: run and args_hash plus routing metadata."""

    args_hash: str
    tool: str  # neutral label stored in tool_calls.tool, e.g. 'p_signals:news_events'
    provider_code: str | None = None
    operation: str | None = None
    route: str | None = None
    fell_back_from: str | None = None


@dataclass(frozen=True)
class Replay:
    """A finished call: its stored response is replayed and nothing is paid again."""

    response: dict[str, Any] | None
    response_ref: str | None
    attempt: int
    cost_usd: Decimal
    credits: Decimal | None
    provider_cost_usd: Decimal | None
    cost_source: str | None
    possible_duplicate: bool


@dataclass(frozen=True)
class Claimed:
    """This worker may make the call; the intent row and the reservation are written."""

    attempt: int
    lease_epoch: int
    reserved_usd: float
    possible_duplicate: bool
    resume_job_id: str | None  # poll this provider job instead of starting a new one


@dataclass(frozen=True)
class InFlight:
    """Another attempt of the same key is in progress; wait, never pay twice."""

    attempt: int


ClaimResult = Replay | Claimed | InFlight


@dataclass(frozen=True)
class Outcome:
    """What a finished attempt records."""

    charged_usd: float
    credits: float | None = None
    provider_cost_usd: float | None = None
    cost_source: str | None = None
    http_status: int | None = None
    latency_ms: int | None = None
    bytes: int | None = None
    response: dict[str, Any] | None = None
    response_ref: str | None = None
    error_class: str | None = None


@dataclass(frozen=True)
class _Attempt:
    attempt: int
    events: frozenset[str]
    owner: str
    epoch: int
    reserved_usd: Decimal
    job_id: str | None


def summarise_attempts(rows: Sequence[Any]) -> list[_Attempt]:
    """Group intent events by attempt (started, submitted, done, failed, abandoned)."""
    by: dict[int, dict[str, Any]] = {}
    for row in rows:
        entry = by.setdefault(
            row.attempt,
            {"events": set(), "owner": "", "epoch": 0, "reserved": Decimal(0), "job": None},
        )
        entry["events"].add(row.event)
        if row.event == "started":
            entry["owner"], entry["epoch"] = row.owner, row.lease_epoch
            entry["reserved"] = row.reserved_usd or Decimal(0)
        if row.event == "submitted":
            entry["job"] = row.provider_job_id
    return [
        _Attempt(n, frozenset(e["events"]), e["owner"], e["epoch"], e["reserved"], e["job"])
        for n, e in sorted(by.items())
    ]


TERMINAL = frozenset({"done", "failed", "abandoned"})


class Ledger:
    """The paid-call ledger: per-run lease, insert-only intent log and pull-once replay."""

    def __init__(
        self,
        txn: TxnFactory,
        clock: Clock,
        *,
        lease_ttl_s: int,
        budget_defaults: budget_mod.BudgetDefaults,
    ) -> None:
        self._txn = txn
        self._clock = clock
        self.lease_ttl_s = lease_ttl_s
        self._budget_defaults = budget_defaults

    # ------------------------------------------------------------------ lease

    async def _upsert_lease(self, s: AsyncSession, run_id: UUID, owner: str) -> int:
        now = self._clock.now()
        row = (
            await s.execute(
                text(
                    "INSERT INTO run_leases (run_id, owner, epoch, expires_at, heartbeat_at) "
                    "VALUES (:r, :o, 1, :exp, :now) "
                    "ON CONFLICT (run_id) DO UPDATE SET "
                    " owner = EXCLUDED.owner,"
                    # a live lease renewed by its owner keeps its epoch; any lease taken anew (a
                    # new owner, or the same owner after a release or expiry: the next slice)
                    # starts a new epoch, so the previous holder's unfinished calls are stale
                    " epoch = CASE WHEN run_leases.owner = EXCLUDED.owner"
                    "   AND run_leases.expires_at > EXCLUDED.heartbeat_at"
                    "   THEN run_leases.epoch ELSE run_leases.epoch + 1 END,"
                    " expires_at = EXCLUDED.expires_at, heartbeat_at = EXCLUDED.heartbeat_at "
                    "WHERE run_leases.owner = EXCLUDED.owner"
                    "   OR run_leases.expires_at <= EXCLUDED.heartbeat_at "
                    "RETURNING epoch"
                ),
                {
                    "r": run_id,
                    "o": owner,
                    "now": now,
                    "exp": now + timedelta(seconds=self.lease_ttl_s),
                },
            )
        ).first()
        if row is None:
            raise LeaseUnavailable()
        return int(row.epoch)

    async def acquire_lease(self, ctx: CallContext, owner: str) -> int:
        """Take or renew the run lease; ``LeaseUnavailable`` while another owner holds it."""
        async with self._txn(ctx.tenant_id) as s:
            return await self._upsert_lease(s, ctx.run_id, owner)

    async def release_lease(self, ctx: CallContext, owner: str) -> None:
        """Give up this worker's lease on the run."""
        async with self._txn(ctx.tenant_id) as s:
            await s.execute(
                text("UPDATE run_leases SET expires_at = :now WHERE run_id = :r AND owner = :o"),
                {"r": ctx.run_id, "o": owner, "now": self._clock.now()},
            )

    # ------------------------------------------------------------------ claim

    async def claim(
        self,
        ctx: CallContext,
        owner: str,
        key: CallKey,
        estimate_usd: float,
        *,
        charge_budget: bool = True,
        cost_known: bool = True,
    ) -> ClaimResult:
        """Replay a done call, wait for one in flight, or claim a new attempt and reserve. A new
        paid attempt first passes the credit cap gate (``spend.gate.decide``) in this same
        transaction; a replay never does (it costs nothing)."""
        async with self._txn(ctx.tenant_id) as s:
            epoch = await self._upsert_lease(s, ctx.run_id, owner)  # also the run mutex
            done = (
                await s.execute(
                    text(
                        "SELECT response, response_ref, attempt, cost_usd, credits, "
                        "provider_cost_usd, cost_source, possible_duplicate "
                        "FROM tool_calls WHERE run_id = :r AND args_hash = :h"
                    ),
                    {"r": ctx.run_id, "h": key.args_hash},
                )
            ).first()
            if done is not None:
                return Replay(
                    response=done.response,
                    response_ref=done.response_ref,
                    attempt=done.attempt or 1,
                    cost_usd=done.cost_usd,
                    credits=done.credits,
                    provider_cost_usd=done.provider_cost_usd,
                    cost_source=done.cost_source,
                    possible_duplicate=bool(done.possible_duplicate),
                )
            attempts = summarise_attempts(
                (
                    await s.execute(
                        text(
                            "SELECT attempt, event, owner, lease_epoch, reserved_usd,"
                            " provider_job_id "
                            "FROM tool_call_events WHERE run_id = :r AND args_hash = :h "
                            "ORDER BY attempt, id"
                        ),
                        {"r": ctx.run_id, "h": key.args_hash},
                    )
                ).all()
            )
            possible_duplicate = False
            resume_job: str | None = None
            next_attempt = 1
            if attempts:
                last = attempts[-1]
                next_attempt = last.attempt + 1
                if not (last.events & TERMINAL):
                    if last.owner == owner and last.epoch == epoch:
                        return InFlight(attempt=last.attempt)
                    # Stale: the worker that started it is gone (lease changed hands).
                    if last.job_id is not None:
                        # The provider job exists; poll it. Nothing was charged for a job
                        # that has not succeeded (sourced for p_org), so release the reserve.
                        resume_job = last.job_id
                        charged = Decimal(0)
                    else:
                        possible_duplicate = True
                        charged = last.reserved_usd
                    await self._insert_event(
                        s,
                        ctx,
                        key,
                        last.attempt,
                        "abandoned",
                        owner,
                        epoch,
                        charged_usd=charged,
                        possible_duplicate=possible_duplicate,
                        error_class="StaleAttempt",
                    )
                    if charge_budget:
                        await budget_mod.true_up(s, ctx.run_id, float(charged - last.reserved_usd))
            if charge_budget:
                await self._cap_gate(s, ctx, key, estimate_usd, cost_known)
                await budget_mod.reserve(s, ctx.run_id, estimate_usd, self._budget_defaults)
            await self._insert_event(
                s,
                ctx,
                key,
                next_attempt,
                "started",
                owner,
                epoch,
                reserved_usd=Decimal(str(round(estimate_usd if charge_budget else 0.0, 6))),
                possible_duplicate=possible_duplicate,
                provider_job_id=resume_job,
            )
            if charge_budget:
                await self._limits(s, ctx)
            return Claimed(
                attempt=next_attempt,
                lease_epoch=epoch,
                reserved_usd=estimate_usd if charge_budget else 0.0,
                possible_duplicate=possible_duplicate,
                resume_job_id=resume_job,
            )

    async def _cap_gate(
        self, s: AsyncSession, ctx: CallContext, key: CallKey, estimate_usd: float, cost_known: bool
    ) -> None:
        """The credit cap decision for one new paid attempt (``CapRefused`` or ``BudgetExceeded``
        when refused). Reads the run's enforcement columns under the run lease held by
        ``claim``."""
        from spend.config import load_spend_config
        from spend.gate import RunCapState, decide
        from spend.money import usd_to_micros

        row = (
            await s.execute(
                text(
                    "SELECT cap_required, cap_version, budget_usd, spent_usd, cap_overshoot_usd,"
                    " cap_sample_ops FROM account_runs WHERE id = :r"
                ),
                {"r": ctx.run_id},
            )
        ).first()
        if row is None:
            raise budget_mod.RunNotFound()
        op_key = f"{key.provider_code}:{key.operation}"
        defective: Any = (
            await s.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM spend_defects WHERE provider_code = :p"
                    " AND operation = :o)"
                ),
                {"p": str(key.provider_code), "o": str(key.operation)},
            )
        ).scalar_one()
        allowed = dict(row.cap_sample_ops or {})
        left: dict[str, int] = {}
        if op_key in allowed:
            used: Any = (
                await s.execute(
                    text(
                        "SELECT count(DISTINCT args_hash) FROM tool_call_events WHERE run_id = :r"
                        " AND provider_code = :p AND operation = :o AND event = 'started'"
                    ),
                    {"r": ctx.run_id, "p": str(key.provider_code), "o": str(key.operation)},
                )
            ).scalar_one()
            left[op_key] = int(allowed[op_key]) - int(used)
        state = RunCapState(
            cap_required=bool(row.cap_required),
            cap_version=row.cap_version,
            budget_micros=usd_to_micros(row.budget_usd) if row.budget_usd is not None else None,
            spent_micros=usd_to_micros(row.spent_usd),
            overshoot_micros=usd_to_micros(row.cap_overshoot_usd),
            sample_calls_left=left,
            defective_free_ops=frozenset({op_key}) if defective else frozenset(),
        )
        decision = decide(
            state,
            op_key,
            usd_to_micros(Decimal(str(round(estimate_usd, 6)))),
            cost_known,
            load_spend_config().free_operations,
        )
        if decision.allowed:
            return
        if decision.reason == "cap_reached":
            raise BudgetExceeded(provider_code=key.provider_code, operation=key.operation)
        raise CapRefused(decision.reason, provider_code=key.provider_code, operation=key.operation)

    async def is_done(self, ctx: CallContext, args_hash_value: str) -> bool:
        """Whether this key already has a finished call (read only; used to skip a provider slot
        for a call that will replay)."""
        async with self._txn(ctx.tenant_id) as s:
            row = (
                await s.execute(
                    text("SELECT 1 FROM tool_calls WHERE run_id = :r AND args_hash = :h"),
                    {"r": ctx.run_id, "h": args_hash_value},
                )
            ).first()
        return row is not None

    async def mark_submitted(
        self, ctx: CallContext, owner: str, key: CallKey, claim: Claimed, job_id: str
    ) -> None:
        """Record a provider job id so a resumed worker polls the same job."""
        async with self._txn(ctx.tenant_id) as s:
            await self._insert_event(
                s,
                ctx,
                key,
                claim.attempt,
                "submitted",
                owner,
                claim.lease_epoch,
                provider_job_id=job_id,
            )

    # ------------------------------------------------------------------ finalise

    async def _lock_and_abandoned(
        self, s: AsyncSession, ctx: CallContext, key: CallKey, attempt: int
    ) -> bool:
        await s.execute(
            text("SELECT 1 FROM run_leases WHERE run_id = :r FOR UPDATE"), {"r": ctx.run_id}
        )
        row = (
            await s.execute(
                text(
                    "SELECT 1 FROM tool_call_events WHERE run_id = :r AND args_hash = :h "
                    "AND attempt = :a AND event = 'abandoned'"
                ),
                {"r": ctx.run_id, "h": key.args_hash, "a": attempt},
            )
        ).first()
        return row is not None

    async def finalize_done(
        self,
        ctx: CallContext,
        owner: str,
        key: CallKey,
        claim: Claimed,
        outcome: Outcome,
        *,
        charge_budget: bool = True,
    ) -> None:
        """Record the paid result. Always written, even by a worker that lost its lease:
        the money was spent and the response is the replay source."""
        async with self._txn(ctx.tenant_id) as s:
            abandoned = await self._lock_and_abandoned(s, ctx, key, claim.attempt)
            # An abandoned attempt already carries its charge; record the result for replay only.
            charged = 0.0 if abandoned else outcome.charged_usd
            await s.execute(
                text(
                    "INSERT INTO tool_calls (tenant_id, run_id, tool, args_hash, response,"
                    " cost_usd,"
                    " provider_code, operation, route, fell_back_from, credits, provider_cost_usd,"
                    " cost_source, http_status, latency_ms, attempt, bytes, possible_duplicate,"
                    " response_ref) "
                    "VALUES ((SELECT app_tenant()), :r, :tool, :h, CAST(:resp AS jsonb), :cost,"
                    " :pc, :op, :route, :fb, :credits, :pcost, :csrc, :status, :lat, :attempt,"
                    " :bytes, :dup, :ref) "
                    "ON CONFLICT (run_id, args_hash) DO NOTHING"
                ),
                {
                    "r": ctx.run_id,
                    "tool": key.tool,
                    "h": key.args_hash,
                    "resp": None if outcome.response is None else canonical_json(outcome.response),
                    "cost": Decimal(str(round(charged, 6))),
                    "pc": key.provider_code,
                    "op": key.operation,
                    "route": key.route,
                    "fb": key.fell_back_from,
                    "credits": _dec(outcome.credits),
                    "pcost": _dec(outcome.provider_cost_usd),
                    "csrc": outcome.cost_source,
                    "status": outcome.http_status,
                    "lat": outcome.latency_ms,
                    "attempt": claim.attempt,
                    "bytes": outcome.bytes,
                    "dup": claim.possible_duplicate,
                    "ref": outcome.response_ref,
                },
            )
            await self._insert_event(
                s,
                ctx,
                key,
                claim.attempt,
                "done",
                owner,
                claim.lease_epoch,
                charged_usd=Decimal(str(round(charged, 6))),
                outcome=outcome,
                possible_duplicate=claim.possible_duplicate,
            )
            if charge_budget and not abandoned:
                await budget_mod.true_up(s, ctx.run_id, charged - claim.reserved_usd)
                await self._limits(s, ctx)
            await self._free_defect(s, ctx, key, charged)

    async def _free_defect(
        self, s: AsyncSession, ctx: CallContext, key: CallKey, charged: float
    ) -> None:
        """Credit cap K14: an operation listed as free that returned a cost is a defect event;
        the gate refuses that operation from then on."""
        from spend.config import load_spend_config

        if charged <= 0:
            return
        if f"{key.provider_code}:{key.operation}" not in load_spend_config().free_operations:
            return
        await s.execute(
            text(
                "INSERT INTO spend_defects (run_id, provider_code, operation, kind, charged_usd)"
                " VALUES (:r, :p, :o, 'free_operation_charged', :c)"
            ),
            {
                "r": ctx.run_id,
                "p": str(key.provider_code),
                "o": str(key.operation),
                "c": Decimal(str(round(charged, 6))),
            },
        )

    async def prepulled_file(self, ctx: CallContext, provider_code: str, operation: str) -> Any:
        """The pre-pulled file registered for this operation of the run, or None (D-31)."""
        from spend.prepulled import lookup

        async with self._txn(ctx.tenant_id) as s:
            return await lookup(s, ctx.run_id, provider_code, operation)

    async def fallback_on_exhaustion(self, ctx: CallContext) -> bool:
        """Whether this tenant allows an alternate route when credits run out: the tenant policy
        when it says so, else the configured default (D-47 supersedes the D-31 default; the
        conflict C16 stays recorded)."""
        from spend.config import load_v2_config
        from spend.exhaustion import tenant_alternate_allowed

        async with self._txn(ctx.tenant_id) as s:
            return await tenant_alternate_allowed(
                s, ctx.tenant_id, load_v2_config().allow_alternate_on_exhaustion
            )

    async def finalize_failed(
        self,
        ctx: CallContext,
        owner: str,
        key: CallKey,
        claim: Claimed,
        outcome: Outcome,
        *,
        charge_budget: bool = True,
    ) -> None:
        """Close a failed attempt with its error class and what was charged."""
        async with self._txn(ctx.tenant_id) as s:
            abandoned = await self._lock_and_abandoned(s, ctx, key, claim.attempt)
            charged = 0.0 if abandoned else outcome.charged_usd
            await self._insert_event(
                s,
                ctx,
                key,
                claim.attempt,
                "failed",
                owner,
                claim.lease_epoch,
                charged_usd=Decimal(str(round(charged, 6))),
                outcome=outcome,
                possible_duplicate=claim.possible_duplicate,
            )
            if charge_budget and not abandoned:
                await budget_mod.true_up(s, ctx.run_id, charged - claim.reserved_usd)
                await self._limits(s, ctx)

    async def _limits(self, s: AsyncSession, ctx: CallContext) -> None:
        """Credit manager v2 (M1): after every reserve and true-up, in the same transaction as
        the counter update, record the soft crossing of the current cap version once."""
        from spend.limits import after_spend_change

        await after_spend_change(s, ctx.run_id, self._clock.now())

    # ------------------------------------------------------------------ reads

    async def ledger_sum(self, ctx: CallContext) -> Decimal:
        """Dollars the ledger says this run was charged (must equal account_runs.spent_usd)."""
        async with self._txn(ctx.tenant_id) as s:
            value: Any = (
                await s.execute(
                    text(
                        "SELECT COALESCE(SUM(e.charged_usd) FILTER "
                        "  (WHERE e.event IN ('done', 'failed', 'abandoned')), 0)"
                        " + COALESCE(SUM(e.reserved_usd) FILTER (WHERE e.event = 'started'"
                        "  AND NOT EXISTS (SELECT 1 FROM tool_call_events t"
                        "   WHERE t.run_id = e.run_id AND t.args_hash = e.args_hash"
                        "   AND t.attempt = e.attempt"
                        "   AND t.event IN ('done', 'failed', 'abandoned'))), 0) AS total "
                        "FROM tool_call_events e WHERE e.run_id = :r"
                    ),
                    {"r": ctx.run_id},
                )
            ).scalar_one()
        return Decimal(value)

    # ------------------------------------------------------------------ internals

    async def _insert_event(
        self,
        s: AsyncSession,
        ctx: CallContext,
        key: CallKey,
        attempt: int,
        event: Literal["started", "submitted", "done", "failed", "abandoned"],
        owner: str,
        epoch: int,
        *,
        reserved_usd: Decimal | None = None,
        charged_usd: Decimal | None = None,
        possible_duplicate: bool = False,
        error_class: str | None = None,
        provider_job_id: str | None = None,
        outcome: Outcome | None = None,
    ) -> None:
        await s.execute(
            text(
                "INSERT INTO tool_call_events (run_id, args_hash, attempt, event, owner,"
                " lease_epoch, provider_code, operation, route, fell_back_from, reserved_usd,"
                " charged_usd, credits, provider_cost_usd, cost_source, http_status, latency_ms,"
                " bytes, possible_duplicate, error_class, provider_job_id) "
                "VALUES (:r, :h, :a, :ev, :o, :ep, :pc, :op, :route, :fb, :res, :chg, :cr,"
                " :pcost, :csrc, :st, :lat, :b, :dup, :err, :job)"
            ),
            {
                "r": ctx.run_id,
                "h": key.args_hash,
                "a": attempt,
                "ev": event,
                "o": owner,
                "ep": epoch,
                "pc": key.provider_code,
                "op": key.operation,
                "route": key.route,
                "fb": key.fell_back_from,
                "res": reserved_usd,
                "chg": charged_usd,
                "cr": _dec(outcome.credits) if outcome else None,
                "pcost": _dec(outcome.provider_cost_usd) if outcome else None,
                "csrc": outcome.cost_source if outcome else None,
                "st": outcome.http_status if outcome else None,
                "lat": outcome.latency_ms if outcome else None,
                "b": outcome.bytes if outcome else None,
                "dup": possible_duplicate,
                "err": error_class
                if error_class is not None
                else (outcome.error_class if outcome else None),
                "job": provider_job_id,
            },
        )


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(round(value, 6)))


__all__ = [
    "CallKey",
    "ClaimResult",
    "Claimed",
    "InFlight",
    "Ledger",
    "LeaseLost",
    "Outcome",
    "Replay",
    "args_hash",
    "canonical_json",
]
