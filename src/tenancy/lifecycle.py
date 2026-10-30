"""Tenant lifecycle (AD2): create, suspend, reactivate and deletion on request, as insert-only
``tenant_events`` (no mutable status column).

* Every change appends the next per-tenant ``seq``; the unique ``(tenant_id, seq)`` makes two
  concurrent changes impossible to both apply (``LifecycleConflict``, re-read and retry).
* ``state`` folds the events: ``suspended`` is the latest of suspended/reactivated; the open
  deletion request (if any) and its phase come from the deletion events.
* Deletion (SaaS 3.1 rule 8): ``request_deletion`` -> ``dry_run`` (counts exactly what would be
  removed, and what is blocked and why) -> ``approve`` by a DIFFERENT person -> ``execute`` in
  bounded batches through the database's ``erase_rows`` (the only delete path; it refuses tables
  outside its allow-list and requests that are not approved and open). Execution is resumable:
  a later pass continues where the last stopped. Ledgers, audit and lifecycle rows are never in
  the plan (``config/deletion.yaml`` ``never``; checked at load). Blocked items keep the request
  open with a ``deletion_blocked`` event naming them.

GUARANTEE(test_lifecycle_changes_are_append_only_and_race_safe)
GUARANTEE(test_deletion_dry_run_lists_exactly_what_execution_removes)
GUARANTEE(test_deletion_needs_a_second_person_and_never_touches_ledgers_or_audit)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

import yaml
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.config import CONFIG_DIR
from account_intelligence.platform.ports import NotConfigured
from tenancy.storage import object_prefixes

Plane = Literal["tenant", "operator", "system"]


class LifecycleError(RuntimeError):
    """A lifecycle change that is not allowed from the current state."""


class LifecycleConflict(LifecycleError):
    """Another change took this sequence number first; re-read and retry."""


@dataclass(frozen=True)
class DeletionPlan:
    """What the cascade erases (ordered), what is blocked (and why), what it never touches."""

    erase: tuple[str, ...]
    blocked: dict[str, str]
    never: frozenset[str]
    batch_size: int
    max_batches_per_pass: int
    # why each table of ``never`` is kept (empty for a plan written as a plain list)
    never_reasons: dict[str, str] = field(default_factory=dict)

    @property
    def digest(self) -> str:
        """SHA-256 of the plan, recorded with the dry run and checked at execution."""
        body = json.dumps(
            {"erase": list(self.erase), "blocked": self.blocked, "batch": self.batch_size},
            sort_keys=True,
        )
        return hashlib.sha256(body.encode()).hexdigest()


@lru_cache
def load_plan() -> DeletionPlan:
    """``config/deletion.yaml``; refuses a plan that would erase a protected table."""
    raw = yaml.safe_load((CONFIG_DIR / "deletion.yaml").read_text())
    plan = DeletionPlan(
        erase=tuple(str(t) for t in raw["erase"]),
        blocked={str(k): str(v) for k, v in raw["blocked"].items()},
        never=frozenset(str(t) for t in raw["never"]),
        batch_size=int(raw["batch_size"]),
        max_batches_per_pass=int(raw["max_batches_per_pass"]),
        never_reasons=(
            {str(k): str(v) for k, v in raw["never"].items()}
            if isinstance(raw["never"], dict)
            else {}
        ),
    )
    check_plan(plan)
    return plan


def check_plan(plan: DeletionPlan) -> None:
    """A plan may never erase a protected table, list a table twice, or erase a blocked one."""
    if overlap := (set(plan.erase) & plan.never) | (set(plan.erase) & set(plan.blocked)):
        raise LifecycleError(f"deletion plan erases protected tables: {sorted(overlap)}")
    if len(set(plan.erase)) != len(plan.erase):
        raise LifecycleError("deletion plan lists a table twice")
    if plan.batch_size < 1 or plan.max_batches_per_pass < 1:
        raise LifecycleError("deletion batches must be positive")


@dataclass
class TenantState:
    """The folded lifecycle of one tenant."""

    seq: int = 0
    suspended: bool = False
    deletion_request: str | None = None
    deletion_phase: str | None = None
    requested_by: str | None = None
    dry_run: dict[str, Any] | None = None
    approvals: list[str] = field(default_factory=list)


async def _events(session: AsyncSession) -> list[Any]:
    return list(
        (
            await session.execute(
                text("SELECT seq, event, actor, detail_json FROM tenant_events ORDER BY seq")
            )
        ).all()
    )


def fold(events: list[Any]) -> TenantState:
    """The state after ``events`` (rows with seq, event, actor, detail_json), in seq order."""
    st = TenantState()
    for e in events:
        st.seq = int(e.seq)
        detail = dict(e.detail_json or {})
        if e.event in ("suspended", "reactivated"):
            st.suspended = e.event == "suspended"
        elif e.event == "deletion_requested":
            st.deletion_request, st.deletion_phase = detail.get("request_id"), "requested"
            st.requested_by, st.dry_run, st.approvals = e.actor, None, []
        elif detail.get("request_id") != st.deletion_request:
            continue
        elif e.event == "deletion_dry_run":
            st.deletion_phase, st.dry_run, st.approvals = "dry_run", detail, []
        elif e.event == "deletion_approved":
            st.deletion_phase = "approved"
            st.approvals.append(e.actor)
        elif e.event in ("deletion_batch", "deletion_blocked"):
            st.deletion_phase = "executing" if e.event == "deletion_batch" else "blocked"
        elif e.event in ("deletion_completed", "deletion_cancelled"):
            st.deletion_request, st.deletion_phase = None, None
    return st


async def state(session: AsyncSession) -> TenantState:
    """The context tenant's folded lifecycle state."""
    return fold(await _events(session))


async def append(
    session: AsyncSession,
    st: TenantState,
    event: str,
    *,
    actor: str,
    plane: Plane,
    now: datetime,
    reason_code: str | None = None,
    detail: dict[str, Any] | None = None,
) -> TenantState:
    """Insert the event at ``st.seq + 1``; LifecycleConflict when another change took it."""
    try:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO tenant_events (seq, event, actor, actor_plane, reason_code,"
                    " detail_json, at) VALUES (:s, :e, :a, :p, :r, CAST(:d AS jsonb), :at)"
                ),
                {
                    "s": st.seq + 1,
                    "e": event,
                    "a": actor,
                    "p": plane,
                    "r": reason_code,
                    "d": json.dumps(detail or {}, sort_keys=True),
                    "at": now,
                },
            )
    except IntegrityError:
        raise LifecycleConflict("another lifecycle change was recorded first") from None
    return await state(session)


def _count(metric: str) -> None:
    """A lifecycle alert counter (never raises; runbooks tenant-suspended, deletion-request)."""
    from obs.setup import record

    record(metric, 1, {})


async def suspend(
    session: AsyncSession, *, actor: str, plane: Plane, reason_code: str, now: datetime
) -> TenantState:
    """Suspend the tenant (sessions and runs are refused within the membership-cache bound)."""
    st = await state(session)
    if st.suspended:
        raise LifecycleError("tenant is already suspended")
    out = await append(
        session, st, "suspended", actor=actor, plane=plane, now=now, reason_code=reason_code
    )
    _count("tenant_suspensions_total")
    return out


async def reactivate(
    session: AsyncSession, *, actor: str, plane: Plane, reason_code: str, now: datetime
) -> TenantState:
    """Lift a suspension."""
    st = await state(session)
    if not st.suspended:
        raise LifecycleError("tenant is not suspended")
    return await append(
        session, st, "reactivated", actor=actor, plane=plane, now=now, reason_code=reason_code
    )


async def request_deletion(
    session: AsyncSession, *, actor: str, plane: Plane, reason_code: str, now: datetime
) -> str:
    """Open a deletion request; returns its id. One open request at a time."""
    st = await state(session)
    if st.deletion_request is not None:
        raise LifecycleError("a deletion request is already open")
    request_id = str(uuid4())
    await append(
        session,
        st,
        "deletion_requested",
        actor=actor,
        plane=plane,
        now=now,
        reason_code=reason_code,
        detail={"request_id": request_id},
    )
    _count("tenant_deletion_requests_total")
    return request_id


async def counts(session: AsyncSession, plan: DeletionPlan) -> dict[str, int]:
    """Rows of the context tenant in every erasable and blocked table (as the runtime role)."""
    out: dict[str, int] = {}
    for table in (*plan.erase, *[t for t in plan.blocked if not t.endswith("_objects")]):
        # the table names come from the validated plan, never from a request
        sql = text(f"SELECT count(*) FROM {table}")  # nosec B608 -- table comes from the validated deletion plan.
        n: int = (await session.execute(sql)).scalar_one()
        out[table] = int(n)
    return out


async def dry_run(
    session: AsyncSession,
    request_id: str,
    *,
    actor: str,
    now: datetime,
    plan: DeletionPlan | None = None,
) -> dict[str, Any]:
    """Count exactly what would be erased and what is blocked; records the report."""
    plan = plan or load_plan()
    st = await state(session)
    if st.deletion_request != request_id or st.deletion_phase not in ("requested", "dry_run"):
        raise LifecycleError("no open deletion request awaiting a dry run")
    found = await counts(session, plan)
    report = {
        "request_id": request_id,
        "plan": plan.digest,
        "erase": {t: found[t] for t in plan.erase},
        "blocked": {t: {"rows": found.get(t), "reason": why} for t, why in plan.blocked.items()},
    }
    await append(
        session, st, "deletion_dry_run", actor=actor, plane="operator", now=now, detail=report
    )
    return report


async def approve(session: AsyncSession, request_id: str, *, actor: str, now: datetime) -> None:
    """The second person's approval; the requester can never approve their own request."""
    st = await state(session)
    if st.deletion_request != request_id or st.deletion_phase != "dry_run":
        raise LifecycleError("the request needs a dry run before approval")
    if actor == st.requested_by:
        raise LifecycleError("the requester cannot approve their own deletion request")
    await append(
        session,
        st,
        "deletion_approved",
        actor=actor,
        plane="operator",
        now=now,
        detail={"request_id": request_id},
    )


async def cancel(session: AsyncSession, request_id: str, *, actor: str, now: datetime) -> None:
    """Close an open request without erasing anything further."""
    st = await state(session)
    if st.deletion_request != request_id:
        raise LifecycleError("no such open deletion request")
    await append(
        session,
        st,
        "deletion_cancelled",
        actor=actor,
        plane="operator",
        now=now,
        detail={"request_id": request_id},
    )


@dataclass
class PassResult:
    """One execution pass: rows erased per table and why it stopped."""

    erased: dict[str, int] = field(default_factory=dict)
    exit_reason: str = ""


class ObjectEraser(Protocol):
    """The part of the object store a deletion needs: remove objects under a prefix."""

    async def delete_prefix(self, prefix: str, limit: int) -> int:
        """Delete at most ``limit`` objects under ``prefix``; returns how many were deleted."""
        ...


OBJECT_BATCH = 500
# one pass deletes at most this many batches of objects per prefix; a later pass continues
OBJECT_BATCHES_PER_PASS = 200


async def erase_objects(
    store: ObjectEraser, tenant_id: UUID, *, batch: int = OBJECT_BATCH
) -> tuple[int, bool]:
    """Delete every stored object of one tenant by prefix (``tenancy.storage.object_prefixes``:
    its own tree, the delivered files, the upload and quarantine prefixes). Returns the number
    deleted and whether every prefix is now empty (False: the pass ended at its cap)."""
    total = 0
    for prefix in object_prefixes(tenant_id):
        # LOOP(object deletion): cap=OBJECT_BATCHES_PER_PASS batches per prefix; exit=a batch
        # shorter than the limit (the prefix is empty) or the cap
        for _ in range(OBJECT_BATCHES_PER_PASS):
            n = await store.delete_prefix(prefix, batch)
            total += n
            if n < batch:
                break
        else:
            return total, False
    return total, True


async def execute(
    session: AsyncSession,
    request_id: str,
    *,
    actor: str,
    now: datetime,
    plan: DeletionPlan | None = None,
    objects: ObjectEraser | None = None,
) -> PassResult:
    """One bounded, resumable pass. Exit reasons: ``completed`` (nothing left and nothing
    blocked), ``blocked`` (erasable rows gone, blocked items remain; the request stays open),
    ``batch_cap`` (call again to continue). With ``objects`` the stored objects of the tenant are
    deleted by prefix after the rows (the five delivered files, workspace zips, raw uploads,
    archived bodies and payloads); without one, or with a store that is not configured, the
    ``raw_payload_objects`` item stays blocked."""
    plan = plan or load_plan()
    st = await state(session)
    if st.deletion_request != request_id or st.deletion_phase not in (
        "approved",
        "executing",
        "blocked",
    ):
        raise LifecycleError("the request is not approved")
    if (st.dry_run or {}).get("plan") != plan.digest:
        raise LifecycleError("the deletion plan changed after the dry run; run it again")
    result = PassResult()
    batches = 0
    for table in plan.erase:
        # LOOP(deletion cascade): cap=max_batches_per_pass erasing batches per pass, plus one
        # empty probe per table; exit=completed, blocked (blocked items remain), batch_cap
        while True:
            if batches >= plan.max_batches_per_pass:
                result.exit_reason = "batch_cap"
                await _record_batches(session, request_id, result, actor, now)
                return result
            n = int(
                (
                    await session.execute(
                        text("SELECT erase_rows(CAST(:r AS uuid), :t, :n)"),
                        {"r": request_id, "t": table, "n": plan.batch_size},
                    )
                ).scalar_one()
            )
            if n == 0:
                break  # already empty: a resumed pass moves straight on
            batches += 1
            result.erased[table] = result.erased.get(table, 0) + n
            if n < plan.batch_size:
                break
    objects_gone = False
    if objects is not None:
        try:
            tenant: Any = (await session.execute(text("SELECT app_tenant()"))).scalar_one()
            gone, objects_gone = await erase_objects(objects, UUID(str(tenant)))
        except NotConfigured:
            gone = 0  # no object store is configured: the item stays blocked
        else:
            if gone:
                result.erased["raw_payload_objects"] = gone
            if not objects_gone:
                result.exit_reason = "batch_cap"
                await _record_batches(session, request_id, result, actor, now)
                return result
    await _record_batches(session, request_id, result, actor, now)
    st = await state(session)
    remaining = await counts(session, plan)
    if objects_gone:
        remaining["raw_payload_objects"] = 0
    blocked = {t: why for t, why in plan.blocked.items() if remaining.get(t, 1)}
    if blocked:
        result.exit_reason = "blocked"
        await append(
            session,
            st,
            "deletion_blocked",
            actor=actor,
            plane="operator",
            now=now,
            detail={"request_id": request_id, "blocked": blocked},
        )
    else:
        result.exit_reason = "completed"
        await append(
            session,
            st,
            "deletion_completed",
            actor=actor,
            plane="operator",
            now=now,
            detail={"request_id": request_id},
        )
    return result


async def _record_batches(
    session: AsyncSession, request_id: str, result: PassResult, actor: str, now: datetime
) -> None:
    st = await state(session)
    await append(
        session,
        st,
        "deletion_batch",
        actor=actor,
        plane="operator",
        now=now,
        detail={"request_id": request_id, "erased": result.erased},
    )


def tenant_is_suspended(st: TenantState) -> bool:
    """Suspension as the identity layer reads it (one rule)."""
    return st.suspended


def request_uuid(value: str) -> UUID:
    """A deletion request id from client input (ValueError when malformed)."""
    return UUID(value)
