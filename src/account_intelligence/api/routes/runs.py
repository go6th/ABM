"""Run endpoints (``/v1/runs``). The tenant comes only from the verified token (W3).

* ``POST /v1/runs``: creates the run through ``runs.lifecycle.create_run`` (both rows, credit
  reservation, seeded tracker, one transaction) and enqueues slice 0. Same key and body replays
  (200 with the first run); same key, other body: 409 (defect 5); no credits: 402.
* ``GET /v1/runs/{id}``: status and the client-safe tracker view (leak-scanned).
* ``POST /v1/runs/{id}/approve``: AWAITING_APPROVAL to RUNNING and the next slice is enqueued
  (a run that requires a credit cap needs ``/cap/approve`` first, ``api.routes.spend``).
* ``POST /v1/runs/{id}/cancel``: ends the run CANCELED and settles the spend so far.
No endpoint accepts a tenant id, and none sends outreach.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity, require_role
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.db.session import tenant_txn
from account_intelligence.runs import lifecycle
from account_intelligence.runs.lifecycle import (
    IdempotencyConflict,
    InsufficientCredits,
    RunNotFound,
    RunRequest,
    RunStateConflict,
)
from account_intelligence.workflow.transitions import RunStatus
from metering import events as usage
from metering import limits
from tenancy import settings
from views import progress
from views.progress import Progress

router = APIRouter(prefix="/runs", tags=["runs"])
DOMAIN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$"


class RunBody(BaseModel):
    """What a client may send. No tenant field exists (and extra fields are refused)."""

    model_config = ConfigDict(extra="forbid")
    domain: str = Field(pattern=DOMAIN, max_length=253)
    depth: Literal["standard", "deep"] = "standard"
    lanes_off: list[str] = Field(default_factory=list, max_length=10)
    pursuing_client: bool = False


def _container(request: Request) -> Any:
    return request.app.state.container


@router.post("", status_code=202)
async def create_run(
    body: RunBody,
    request: Request,
    idem: str = Header(alias="Idempotency-Key", min_length=8, max_length=64),
    ident: Identity = Depends(require_role("admin", "seller")),
) -> dict[str, Any]:
    """Create a run (both rows, reservation, tracker in one transaction) and enqueue slice 0."""
    container = _container(request)
    try:
        async with tenant_txn(ident.tenant_id) as s:
            run_input, version = await _run_input(s, body, ident, container)
            req = RunRequest(body.domain, body.depth, idem, run_input)
            now = container.clock.now()
            _, new_account = await lifecycle.add_account(s, ident.tenant_id, body.domain)
            if new_account:
                await limits.consume(s, "accounts", now)
                await usage.record(s, "account_added", body.domain, now)
            deps_tracker = await _tracker_for(container, s)
            created = await lifecycle.create_run(
                s, ident.tenant_id, req, container.limits, deps_tracker
            )
            if not created.replayed:
                await settings.pin_run(s, created.run_id, version)
                await limits.consume(s, "runs_per_period", now)
                await usage.record(s, "run_started", created.run_id, now)
            await audit_action(
                s,
                request,
                audit_class="run",
                action="start_replay" if created.replayed else "start",
                resource_type="run",
                resource_id=created.run_id,
            )
    except limits.LimitReached as e:
        raise limits.limit_problem(e) from None
    except IdempotencyConflict:
        raise HTTPException(409, "idempotency key reused with a different body") from None
    except InsufficientCredits:
        raise HTTPException(402, "insufficient credits") from None
    except RunStateConflict:
        raise HTTPException(409, "a concurrent request created this run; retry") from None
    if not created.replayed:
        await container.scheduler.enqueue_slice(ident.tenant_id, created.run_id, 0)
    return {"run_id": str(created.run_id), "status": created.status, "replayed": created.replayed}


async def _run_input(
    s: Any, body: RunBody, ident: Identity, container: Any
) -> tuple[dict[str, Any], int]:
    """The run's frozen input from the tenant's current settings (TN5): lanes the tenant switched
    off, the people geography, the exclusions (applied at pull time) and the pursuing client;
    an excluded company is refused. Returns the input and the settings version it used."""
    now = container.clock.now()
    try:
        current = await settings.ensure_saved(s, actor=ident.user_id, now=now)
    except settings.SettingsInvalid:
        raise APIProblem(
            409,
            "settings_invalid",
            "Settings need attention",
            "An admin must save the tenant settings again.",
        ) from None
    rule = settings.ExclusionFilter.of(current.settings)
    if rule.excludes_company(body.domain):
        raise APIProblem(
            409,
            "excluded_company",
            "Excluded company",
            "This company is on the tenant's exclusion list.",
        )
    lanes, _, _ = settings.platform_options()
    off = set(body.lanes_off) | (set(lanes) - set(current.settings.platform_lanes))
    run_input: dict[str, Any] = {"lanes_off": sorted(off), "pursuing_client": body.pursuing_client}
    if body.pursuing_client and current.settings.pursuing_client:
        run_input["pursuing_client"] = current.settings.pursuing_client
    if current.settings.people_scope.regions:
        run_input["people_regions"] = list(current.settings.people_scope.regions)
    if rule.people or rule.companies:
        run_input.update(rule.run_input())
    return run_input, current.version


async def _tracker_for(container: Any, session: Any) -> Any:
    from account_intelligence.grading.config import load_grading_config
    from account_intelligence.tracker.adapters import LedgerCallAdapter, RunInputAdapter
    from account_intelligence.tracker.repo import CoverageRepo
    from account_intelligence.tracker.service import TrackerService

    return TrackerService(
        CoverageRepo(session),
        LedgerCallAdapter(session, load_grading_config().category_by_operation),
        RunInputAdapter(session),
        owners=container.spec.coverage_owners,
    )


@router.get("/{run_id}")
async def get_run(
    run_id: UUID,
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    ident: Identity = Depends(require_role("admin", "seller", "viewer")),
) -> Response:
    """The run's status, end reason and client-safe coverage view (leak-scanned)."""
    container = _container(request)
    async with tenant_txn(ident.tenant_id) as s:
        row = (
            await s.execute(
                text("SELECT status, finished_reason FROM account_runs WHERE id = :r"),
                {"r": run_id},
            )
        ).first()
        if row is None:
            raise HTTPException(404, "run not found")
        view = await (await _tracker_for(container, s)).view(run_id, "client")
    payload = {
        "run_id": str(run_id),
        "status": row.status,
        "reason": row.finished_reason,
        "coverage": view,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    etag = '"' + hashlib.sha256(encoded.encode()).hexdigest() + '"'
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(payload, headers={"ETag": etag})


@router.post("/{run_id}/approve")
async def approve(
    run_id: UUID, request: Request, ident: Identity = Depends(require_role("admin", "seller"))
) -> dict[str, str]:
    """Approve a run waiting at the approval gate and enqueue its next slice. A run that requires
    a credit cap needs its cap approved first (``/cap/approve``, K2): 409 otherwise."""
    container = _container(request)
    try:
        async with tenant_txn(ident.tenant_id) as s:
            cap = (
                await s.execute(
                    text("SELECT cap_required, cap_version FROM account_runs WHERE id = :r"),
                    {"r": run_id},
                )
            ).first()
            if cap is not None and cap.cap_required and cap.cap_version is None:
                raise HTTPException(409, "approve the credit cap first (POST .../cap/approve)")
            await lifecycle.move(s, run_id, RunStatus.AWAITING_APPROVAL, RunStatus.RUNNING)
            used: int = (
                await s.execute(
                    text("SELECT slices_used FROM account_runs WHERE id = :r"), {"r": run_id}
                )
            ).scalar_one()
            await audit_action(
                s,
                request,
                audit_class="run",
                action="approve",
                resource_type="run",
                resource_id=run_id,
            )
    except (RunNotFound, RunStateConflict):
        raise HTTPException(409, "run is not awaiting approval") from None
    await container.scheduler.enqueue_slice(ident.tenant_id, run_id, int(used))
    return {"run_id": str(run_id), "status": RunStatus.RUNNING.value}


@router.post("/{run_id}/cancel")
async def cancel(
    run_id: UUID, request: Request, ident: Identity = Depends(require_role("admin", "seller"))
) -> dict[str, str]:
    """Cancel a run: it ends CANCELED, unset rows are swept and the spend so far is settled."""
    container = _container(request)
    try:
        async with tenant_txn(ident.tenant_id) as s:
            status, _ = await lifecycle.finish(
                s,
                await _tracker_for(container, s),
                run_id,
                RunStatus.CANCELED,
                reason="canceled_by_user",
            )
            await audit_action(
                s,
                request,
                audit_class="run",
                action="cancel",
                resource_type="run",
                resource_id=run_id,
            )
    except RunNotFound:
        raise HTTPException(404, "run not found") from None
    except ValueError:
        raise HTTPException(409, "run cannot be canceled from its state") from None
    return {"run_id": str(run_id), "status": status}


register("GET", "/v1/runs/{run_id}/progress", tenant_route("read_public", "read"))
register("GET", "/v1/runs/{run_id}/events", tenant_route("read_public", "read"))


async def _progress(container: Any, tenant_id: UUID, run_id: UUID) -> Progress:
    async with tenant_txn(tenant_id) as s:
        row = (
            await s.execute(text("SELECT status FROM account_runs WHERE id = :r"), {"r": run_id})
        ).first()
        if row is None:
            raise HTTPException(404, "not found")
        rows = (
            await s.execute(
                text("SELECT row_key, status FROM coverage_rows WHERE run_id = :r"),
                {"r": run_id},
            )
        ).all()
    stages = [(st.id, st.name) for st in container.spec.stages]
    return progress.compute(
        str(run_id),
        row.status,
        stages,
        container.spec.coverage_owners,
        [(r.row_key, r.status) for r in rows],
    )


@router.get("/{run_id}/progress")
async def run_progress(
    run_id: UUID,
    request: Request,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    ident: Identity = Depends(require_role("admin", "seller", "viewer")),
) -> Response:
    """Stages done of stages (``3 of 7``), with an ETag for cheap polling."""
    got = await _progress(_container(request), ident.tenant_id, run_id)
    if if_none_match == got.etag:
        return Response(status_code=304, headers={"ETag": got.etag})
    return JSONResponse(got.as_json(), headers={"ETag": got.etag})


@router.get("/{run_id}/events")
async def run_events(
    run_id: UUID,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ident: Identity = Depends(require_role("admin", "seller", "viewer")),
) -> StreamingResponse:
    """Server-sent progress events: one event per change, a heartbeat, a bounded duration (the
    client reconnects with Last-Event-ID and is authorised again)."""
    container = _container(request)
    first = await _progress(container, ident.tenant_id, run_id)  # 404 before streaming
    cfg = progress_config()

    async def stream() -> AsyncIterator[bytes]:
        """Progress events, heartbeats and a final reconnect hint, within the duration cap."""
        last = last_event_id
        current: Progress | None = first
        started = time.monotonic()
        beat = started
        # LOOP(progress stream): cap=sse_max_duration_s seconds, polled every sse_poll_s;
        # exit=finished run, max duration, client gone
        while time.monotonic() - started < cfg["sse_max_duration_s"]:
            if current is not None and current.etag != last:
                body = json.dumps(current.as_json(), separators=(",", ":"))
                yield f"id: {current.etag}\nevent: progress\ndata: {body}\n\n".encode()
                last = current.etag
                beat = time.monotonic()
                if current.finished:
                    return
            elif time.monotonic() - beat >= cfg["sse_heartbeat_s"]:
                yield b": heartbeat\n\n"
                beat = time.monotonic()
            if await request.is_disconnected():
                return
            await asyncio.sleep(cfg["sse_poll_s"])
            current = await _progress(container, ident.tenant_id, run_id)
        yield b"event: reconnect\ndata: {}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


def progress_config() -> dict[str, float]:
    """``config/views.yaml`` progress section (read on each stream)."""
    import yaml

    from account_intelligence.config import CONFIG_DIR

    raw = yaml.safe_load((CONFIG_DIR / "views.yaml").read_text())["progress"]
    return {k: float(v) for k, v in raw.items()}
