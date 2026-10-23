"""Run endpoints (``/v1/runs``). The tenant comes only from the verified token (W3).

* ``POST /v1/runs``: creates the run through ``runs.lifecycle.create_run`` (both rows, credit
  reservation, seeded tracker, one transaction) and enqueues slice 0. Same key and body replays
  (200 with the first run); same key, other body: 409 (defect 5); no credits: 402.
* ``GET /v1/runs/{id}``: status and the client-safe tracker view (leak-scanned).
* ``POST /v1/runs/{id}/approve``: AWAITING_APPROVAL to RUNNING and the next slice is enqueued
  (a run that requires a credit cap needs ``/cap/approve`` first, ``api.routes.spend``).
* ``POST /v1/runs/{id}/cancel``: ends the run CANCELED and settles the spend so far.
* ``mode`` (D-102): ``full`` (default when absent), ``pull`` (Phase 1 only) or ``build`` (Phase 2
  only, with ``parent_run_id``); ``GET /v1/runs/pull-option`` says whether Phase 2 only may be
  offered.
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

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity, require_role
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.routes.accounts import exclusion_rule
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica.workspace import can_resume, resume_context
from account_intelligence.runs import lifecycle
from account_intelligence.runs.lifecycle import (
    IdempotencyConflict,
    InsufficientCredits,
    RunNotFound,
    RunRequest,
    RunStateConflict,
)
from account_intelligence.workflow.transitions import RunStatus
from agents.adapters import build_parent, pull_option, run_mode_columns
from agents.config import skill_config
from agents.errors import InvalidMode
from agents.modes import RunMode, RunPlan, plan_run, resolve_mode
from metering import events as usage
from metering import limits
from opendoor.api import decode_cursor, encode_cursor
from tenancy import settings
from views import progress
from views.progress import Progress
from views.token_use import token_use

router = APIRouter(prefix="/runs", tags=["runs"])
# the label every run and every result carries, by input mode
SOURCE_LABELS = {"company": "Public research", "file": "From your files"}
DOMAIN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$"


class CompanyInput(BaseModel):
    """Input mode "company": research a company from public sources (the existing pipeline)."""

    model_config = ConfigDict(extra="forbid")
    company_name: str = Field(min_length=1, max_length=200)
    domain: str | None = Field(default=None, pattern=DOMAIN, max_length=253)
    # the run's spend limit in USD (the cap approval still applies); absent: the default budget
    credit_cap: float | None = Field(default=None, gt=0, le=10000)


class FileInput(BaseModel):
    """Input mode "file": research only the uploaded documents (public enrichment is opt-in)."""

    model_config = ConfigDict(extra="forbid")
    document_ids: list[UUID] = Field(min_length=1, max_length=100)
    account_id: UUID | None = None
    enrich_public: bool = False
    credit_cap: float | None = Field(default=None, gt=0, le=10000)


class RunBody(BaseModel):
    """What a client may send. No tenant field exists (and extra fields are refused). Exactly one
    input mode: ``domain`` alone (company mode, unchanged), ``input_mode: company`` with
    ``company``, or ``input_mode: file`` with ``file``; mixed or empty bodies are refused."""

    model_config = ConfigDict(extra="forbid")
    input_mode: Literal["company", "file"] | None = None
    company: CompanyInput | None = None
    file: FileInput | None = None
    domain: str | None = Field(default=None, pattern=DOMAIN, max_length=253)
    depth: Literal["standard", "deep"] = "standard"
    lanes_off: list[str] = Field(default_factory=list, max_length=10)
    pursuing_client: bool = False
    # D-102: an explicit typed choice; absent means "full" (both phases). Never parsed from text.
    mode: Literal["full", "pull", "build"] | None = None
    parent_run_id: UUID | None = None
    # G16: stale pulled rows the requester accepts for a Phase 2 only run (else refresh first)
    acknowledged_stale_rows: list[str] = Field(default_factory=list, max_length=40)
    # Durable runs: continue an earlier run of the same account; every finished call and model
    # reply of that run is reused at cost 0 whatever its age (only missing work is paid for).
    resume_from: UUID | None = None


def _container(request: Request) -> Any:
    return request.app.state.container


@router.post("", response_model=C.RunCreated, status_code=202)
async def create_run(
    body: RunBody,
    request: Request,
    idem: str = Header(alias="Idempotency-Key", min_length=8, max_length=64),
    ident: Identity = Depends(require_role("admin", "seller")),
) -> dict[str, Any]:
    """Create a run (both rows, reservation, tracker in one transaction) and enqueue slice 0."""
    container = _container(request)
    mode = _input_mode(body)
    if mode == "file":
        from account_intelligence.api.routes.file_runs import create_file_run

        return await create_file_run(body, request, idem, ident, container)
    if body.domain is None:
        async with tenant_txn(ident.tenant_id) as s:
            body = body.model_copy(update={"domain": await _company_domain(s, body)})
    assert body.domain is not None
    try:
        async with tenant_txn(ident.tenant_id) as s:
            run_input, version = await _run_input(s, body, ident, container)
            plan = await _mode_plan(s, body)
            run_input["mode"] = plan.mode.value
            if plan.parent_run_id is not None:
                run_input["parent_run_id"] = str(plan.parent_run_id)
            if body.acknowledged_stale_rows:
                run_input["acknowledged_stale_rows"] = sorted(set(body.acknowledged_stale_rows))
            if body.resume_from is not None:
                run_input["resume_from"] = str(await _resumable(s, body.resume_from, body.domain))
            req = RunRequest(body.domain, body.depth, idem, run_input)
            now = container.clock.now()
            account_id, new_account = await lifecycle.add_account(
                s, ident.tenant_id, body.domain, added_by=ident.user_id
            )
            if body.company is not None:
                await lifecycle.name_account(s, account_id, body.company.company_name)
            if new_account:
                await limits.consume(s, "accounts", now)
                await usage.record(s, "account_added", body.domain, now)
            deps_tracker = await _tracker_for(container, s)
            run_limits = container.limits
            if body.company is not None and body.company.credit_cap is not None:
                from dataclasses import replace
                from decimal import Decimal

                cap = Decimal(str(round(body.company.credit_cap, 2)))
                run_limits = replace(run_limits, budget_usd=cap)
                req = RunRequest(
                    body.domain, body.depth, idem, {**run_input, "credit_cap": float(cap)}
                )
            created = await lifecycle.create_run(
                s,
                ident.tenant_id,
                req,
                run_limits,
                deps_tracker,
                lifecycle.CreditJob(kind="new_research", scope="full", actor=ident.user_id),
            )
            if not created.replayed:
                await run_mode_columns(s, created.run_id, plan)
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


def _input_mode(body: RunBody) -> str:
    """Exactly one input mode (see ``RunBody``); 422 ``input_mode_mixed`` or ``_empty``."""
    company = body.company is not None
    has_file = body.file is not None
    mode = body.input_mode
    if mode is None:
        mode = "file" if has_file else "company"
    mixed = (mode == "file" and (company or body.domain is not None)) or (
        mode == "company" and has_file
    )
    if mixed:
        raise APIProblem(
            422,
            "input_mode_mixed",
            "One input mode per run",
            "Send either a company (name and domain) or uploaded documents, not both.",
        )
    empty = (mode == "file" and not has_file) or (
        mode == "company" and not company and body.domain is None
    )
    if empty:
        raise APIProblem(
            422,
            "input_mode_empty",
            "Nothing to research",
            "Send a company name and domain, or the uploaded documents to research.",
        )
    if (
        body.company is not None
        and body.domain is not None
        and body.company.domain
        and body.company.domain != body.domain
    ):
        raise APIProblem(422, "input_mode_mixed", "Two domains", "Send the company's domain once.")
    return mode


async def _company_domain(s: Any, body: RunBody) -> str:
    """The domain of company mode: the one sent, or the only account with that name."""
    assert body.company is not None
    if body.company.domain:
        return body.company.domain
    rows = (
        await s.execute(
            text("SELECT domain FROM accounts WHERE lower(name) = lower(:n) LIMIT 2"),
            {"n": body.company.company_name.strip()},
        )
    ).all()
    if len(rows) != 1:
        raise APIProblem(
            422,
            "domain_required",
            "Company domain needed",
            "Add the company's web domain (for example acme.com) so the right company is"
            " researched.",
        )
    return str(rows[0].domain)


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


async def _resumable(s: Any, run_id: UUID, domain: str) -> UUID:
    """The run to resume: one of this tenant (RLS), for the same account; 422 otherwise."""
    row = (
        await s.execute(
            text(
                "SELECT a.domain FROM account_runs r JOIN accounts a ON a.id = r.account_id"
                " WHERE r.id = :r"
            ),
            {"r": run_id},
        )
    ).first()
    if row is None or str(row.domain).lower() != domain.lower():
        raise APIProblem(
            422,
            "resume_run_unknown",
            "Run to resume not found",
            "resume_from must name a run of this workspace for the same account.",
        )
    return run_id


async def _mode_plan(s: Any, body: RunBody) -> RunPlan:
    """D-102 / G16: the run's mode, parent and config pin. A Phase 2 only run needs a pull of the
    same account by this tenant with a stored manifest; otherwise ``no_pulled_data`` (422) and
    nothing is written. It never falls back to running Phase 1."""
    mode = resolve_mode(body.mode)
    parent = await build_parent(s, body.parent_run_id) if body.parent_run_id else None
    current = skill_config().config_version
    if mode is RunMode.BUILD and (
        parent is None or parent.domain != body.domain or not parent.has_manifest
    ):
        raise APIProblem(
            422,
            "no_pulled_data",
            "No pulled data",
            "Run Phase 1 for this account first, or choose Run both.",
        )
    try:
        return plan_run(
            mode,
            parent_run_id=parent.run_id if parent else None,
            parent_mode=resolve_mode(parent.mode) if parent else None,
            parent_config_version=(parent.config_version or current) if parent else None,
            requested_config_version=None,
            current_config_version=current,
        )
    except InvalidMode as error:
        raise APIProblem(422, "invalid_mode", "Invalid run mode", error.detail) from None


register("GET", "/v1/runs", tenant_route("read_public", "read"))


@router.get("", response_model=C.RunPage)
async def list_runs(
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=30, ge=1, le=100),
    ident: Identity = Depends(require_role("admin", "analyst", "seller", "viewer")),
) -> dict[str, Any]:
    """The tenant's runs, newest first (keyset by creation time and id), with the client status
    label. Excluded companies are hidden as in the account list."""
    try:
        at, rid = decode_cursor(cursor) if cursor else (None, None)
    except ValueError:
        raise HTTPException(422, "bad cursor") from None
    async with tenant_txn(ident.tenant_id) as s:
        rule = await exclusion_rule(s)
        rows = (
            await s.execute(
                text(
                    "SELECT r.id, r.status, r.created_at, r.input_mode, a.domain"
                    " FROM account_runs r JOIN accounts a ON a.id = r.account_id"
                    " WHERE (CAST(:at AS timestamptz) IS NULL"
                    " OR (r.created_at, r.id) < (CAST(:at AS timestamptz), CAST(:id AS uuid)))"
                    " ORDER BY r.created_at DESC, r.id DESC LIMIT :n"
                ),
                {"at": at, "id": rid, "n": limit + 1},
            )
        ).all()
    page = rows[:limit]
    return {
        "runs": [
            {
                "run_id": str(r.id),
                "domain": r.domain,
                "status": r.status,
                "label": progress.STATUS_LABELS.get(r.status, r.status),
                "created_at": r.created_at.isoformat(),
                "input_mode": r.input_mode,
                "source_label": SOURCE_LABELS[r.input_mode],
            }
            for r in page
            if not rule.excludes_company(r.domain)
        ],
        "next_cursor": encode_cursor(page[-1].created_at, page[-1].id)
        if len(rows) > limit
        else None,
    }


@router.get("/pull-option", response_model=C.PullOption, response_model_exclude_unset=True)
async def get_pull_option(
    domain: str,
    ident: Identity = Depends(require_role("admin", "seller", "viewer")),
) -> dict[str, Any]:
    """Whether "Phase 2 only" may be offered for the account, with the pull's date and coverage
    (work item 2). Counts only; no content, provider or cost."""
    async with tenant_txn(ident.tenant_id) as s:
        option = await pull_option(s, domain.strip().lower().rstrip("."))
    return {"available": option is not None, **(option or {})}


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


@router.get("/{run_id}", responses={200: {"model": C.RunStatusView}})
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
                text(
                    "SELECT r.status, r.finished_reason, r.stop_detail, r.input_mode, a.domain,"
                    " r.models, r.stalled_at, " + resume_context("r") + ","
                    " coalesce(r.run_input->>'linked_file_run', (SELECT c.id::text FROM"
                    " account_runs c WHERE c.run_input->>'linked_file_run' = r.id::text"
                    " LIMIT 1)) AS linked"
                    " FROM account_runs r JOIN accounts a ON a.id = r.account_id WHERE r.id = :r"
                ),
                {"r": run_id},
            )
        ).first()
        if row is None:
            raise HTTPException(404, "run not found")
        view = await (await _tracker_for(container, s)).view(run_id, "client")
        stored = await _stored_counts(s, run_id)
        gate = (
            await s.execute(
                text(
                    "SELECT verdict, defects FROM run_gate_results WHERE run_id = :r"
                    " ORDER BY id DESC LIMIT 1"
                ),
                {"r": run_id},
            )
        ).first()
        # the router's ledger (llm_calls) or the research graph's reply log (model_reply_log,
        # written while the router is off): the larger is the run's use (audit 2026-10-21: the
        # meter read llm_calls only and showed 0 for graph runs)
        use = await token_use(s, run_id)
        tokens_used = use["total"]
    payload = {
        "domain": row.domain,
        "run_id": str(run_id),
        "status": row.status,
        "reason": row.finished_reason,
        "coverage": view,
        "stop_detail": row.stop_detail,
        "stored": stored,
        "missing": _missing(view),
        "input_mode": row.input_mode,
        "source_label": SOURCE_LABELS[row.input_mode],
        "linked_run_id": row.linked,
        "models_line": models_line(row.models),
        "can_resume": can_resume(
            row.status, row.finished_reason, row.stalled_at, row.is_latest, row.job_open
        ),
        "tokens_used": tokens_used,
        "token_use": use,
        "gate": (
            {"verdict": gate.verdict, "defects": list(gate.defects)[:50]}
            if gate is not None
            else None
        ),
    }
    payload = C.RunStatusView.model_validate(payload).model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    etag = '"' + hashlib.sha256(encoded.encode()).hexdigest() + '"'
    if if_none_match == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(payload, headers={"ETag": etag})


_DONE_TEXT = frozenset({"Done", "None"})


def _missing(view: dict[str, Any]) -> list[dict[str, Any]]:
    """Coverage rows that are not Done or None, with their (already client-safe) reason."""
    return [
        {"row": r["row"], "status": r["status"], "reason": r.get("reason")}
        for r in view.get("rows", [])
        if r.get("status") not in _DONE_TEXT
    ]


async def _stored_counts(s: Any, run_id: UUID) -> dict[str, int]:
    """What the run holds so far (every count names its base in the contract)."""
    row = (
        await s.execute(
            text(
                "SELECT"
                " (SELECT count(*) FROM tool_calls WHERE run_id = :r AND response_ref IS NOT NULL"
                "   AND cost_source IS DISTINCT FROM 'cache') AS paid_calls,"
                " (SELECT count(*) FROM tool_calls WHERE run_id = :r AND cost_source = 'cache')"
                "   AS cached_calls,"
                " (SELECT count(*) FROM model_reply_log WHERE run_id = :r"
                "   AND served_from IN ('model', 'cache')) AS model_replies,"
                " (SELECT count(*) FROM facts WHERE run_id = :r AND status = 'current'"
                "   AND visibility = 'public') AS facts,"
                " (SELECT count(*) FROM abm_distill_outputs WHERE run_id = :r"
                "   AND audience = 'client') AS drafts,"
                " (SELECT count(*) FROM deliverables WHERE run_id = :r) AS deliverables"
            ),
            {"r": run_id},
        )
    ).one()
    return {
        "paid_calls": int(row.paid_calls),
        "cached_calls": int(row.cached_calls),
        "model_replies": int(row.model_replies),
        "facts": int(row.facts),
        "drafts": int(row.drafts),
        "deliverables": int(row.deliverables),
    }


@router.get("/{run_id}/collected", response_model=C.RunCollected)
async def get_collected(
    run_id: UUID,
    request: Request,
    ident: Identity = Depends(require_role("admin", "seller", "viewer")),
) -> dict[str, Any]:
    """Everything the run holds so far (a PARTIAL run included): finished pulls by tool category
    (no provider is named), public graded facts, the client distill drafts, and the coverage rows
    still missing with their reasons. Every string is leak-scanned like the coverage view."""
    from account_intelligence.grading.config import load_grading_config
    from account_intelligence.safety.client_output import assert_client_safe

    container = _container(request)
    categories = load_grading_config().category_by_operation
    async with tenant_txn(ident.tenant_id) as s:
        row = (
            await s.execute(
                text("SELECT status, finished_reason, stop_detail FROM account_runs WHERE id = :r"),
                {"r": run_id},
            )
        ).first()
        if row is None:
            raise HTTPException(404, "run not found")
        view = await (await _tracker_for(container, s)).view(run_id, "client")
        stored = await _stored_counts(s, run_id)
        pulls = (
            await s.execute(
                text(
                    "SELECT operation, count(*) AS calls,"
                    " count(*) FILTER (WHERE cost_source = 'cache') AS cached,"
                    " min(response->>'retrieved_at') AS retrieved_from,"
                    " max(response->>'retrieved_at') AS retrieved_to"
                    " FROM tool_calls WHERE run_id = :r AND response_ref IS NOT NULL"
                    " AND response->>'error_class' IS NULL GROUP BY operation ORDER BY operation"
                ),
                {"r": run_id},
            )
        ).all()
        facts = (
            await s.execute(
                text(
                    "SELECT id, attribute_key, value_text, product_grade, lane,"
                    " may_state_to_prospect FROM facts WHERE run_id = :r AND status = 'current'"
                    " AND visibility = 'public' ORDER BY attribute_key, id"
                ),
                {"r": run_id},
            )
        ).all()
        draft = (
            await s.execute(
                text(
                    "SELECT output FROM abm_distill_outputs WHERE run_id = :r"
                    " AND audience = 'client' ORDER BY created_at DESC LIMIT 1"
                ),
                {"r": run_id},
            )
        ).scalar_one_or_none()
    by_category: dict[str, dict[str, Any]] = {}
    for p in pulls:
        cat = categories.get(str(p.operation), "other")
        entry = by_category.setdefault(
            cat,
            {
                "tool_category": cat,
                "calls": 0,
                "cached": 0,
                "retrieved_from": None,
                "retrieved_to": None,
            },
        )
        entry["calls"] += int(p.calls)
        entry["cached"] += int(p.cached)
        entry["retrieved_from"] = min(filter(None, (entry["retrieved_from"], p.retrieved_from)))
        entry["retrieved_to"] = max(filter(None, (entry["retrieved_to"], p.retrieved_to)))
    drafts: dict[str, list[dict[str, Any]]] = {"hypotheses": [], "questions": [], "narrative": []}
    stored_draft: dict[str, Any] = dict(draft) if isinstance(draft, dict) else {}
    for section in drafts:
        for claim in stored_draft.get(section) or []:
            drafts[section].append(
                {"text": str(claim.get("text", "")), "fact_ids": list(claim.get("fact_ids", []))}
            )
    payload = {
        "run_id": str(run_id),
        "status": row.status,
        "reason": row.finished_reason,
        "stop_detail": row.stop_detail,
        "stored": stored,
        "pulls": sorted(by_category.values(), key=lambda e: e["tool_category"]),
        "facts": [
            {
                "fact_id": str(f.id),
                "attribute": f.attribute_key,
                "value": f.value_text,
                "grade": f.product_grade,
                "lane": f.lane,
                "may_state_to_prospect": bool(f.may_state_to_prospect),
            }
            for f in facts
        ],
        "drafts": drafts,
        "missing": _missing(view),
    }
    assert_client_safe([json.dumps(payload, default=str)])
    return payload


@router.post("/{run_id}/approve", response_model=C.RunState)
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


@router.post("/{run_id}/cancel", response_model=C.RunState)
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
            await s.execute(
                text(
                    "SELECT status, (run_mode IN ('pull', 'build') OR (input_mode = 'company'"
                    " AND (run_input ? 'job_id' OR run_input ? 'linked_file_run'))) AS abm"
                    " FROM account_runs WHERE id = :r"
                ),
                {"r": run_id},
            )
        ).first()
        if row is None:
            raise HTTPException(404, "not found")
        rows = (
            await s.execute(
                text("SELECT row_key, status FROM coverage_rows WHERE run_id = :r"),
                {"r": run_id},
            )
        ).all()
        # the graph's finished nodes: the latest checkpoint's versions_seen (thread
        # '<tenant>:<run>', agents.service; RLS keeps it to the tenant)
        seen = (
            await s.execute(
                text(
                    "SELECT array(SELECT jsonb_object_keys(checkpoint -> 'versions_seen'))"
                    " FROM checkpoints WHERE thread_id = :t AND checkpoint_ns = ''"
                    " ORDER BY checkpoint_id DESC LIMIT 1"
                ),
                {"t": f"{tenant_id}:{run_id}"},
            )
        ).scalar_one_or_none()
    nodes: set[str] = set(seen or ()) & progress.ABM_NODES
    abm = bool(row.abm) or bool(nodes & progress.ABM_ONLY)
    stages = [(st.id, st.name) for st in container.spec.stages]
    return progress.compute(
        str(run_id),
        row.status,
        stages,
        container.spec.coverage_owners,
        [(r.row_key, r.status) for r in rows],
        nodes_done=nodes if abm else None,
    )


@router.get("/{run_id}/progress", responses={200: {"model": C.RunProgress}})
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
    body = C.RunProgress.model_validate(got.as_json()).model_dump(mode="json")
    return JSONResponse(body, headers={"ETag": got.etag})


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
        # the browser reconnects this soon after the stream ends at its duration cap
        yield f"retry: {int(cfg['sse_poll_s'] * 1000)}\n\n".encode()
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


def models_line(raw: Any) -> str | None:
    """The run's models in words, with any step served by a fallback (None: none recorded)."""
    if not raw:
        return None
    from llm.choice import RunModels, catalogue

    models = RunModels.from_json(raw)
    cat = catalogue()

    def name(model_id: str) -> str:
        found = cat.get(model_id)
        return found.label if found is not None else model_id

    line = f"Research: {name(models.research)} · Analysis: {name(models.analysis)}"
    if models.fallbacks:
        steps = len(models.fallbacks)
        line += f" · {steps} step{'s' if steps != 1 else ''} used {name(models.fallbacks[0]['to'])}"
    return line
