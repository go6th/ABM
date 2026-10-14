"""Accounts and their tables (``/v1/accounts``; VW1 to VW3, TN2, TN5).

* ``GET /v1/accounts``: the tenant's accounts with their latest run (excluded companies are
  hidden: render time).
* ``POST /v1/accounts``: add an account (refused for an excluded company: pull time).
* ``GET /v1/accounts/{domain}/tables/{table}``: one page of a client table (people, jobs, tech,
  facts, coverage) for the account's latest finished run (or ``run_id``), filtered, sorted and
  searched in SQL from a validated spec or a saved view, keyset paginated. ``{domain}`` and any
  view id are resolved INSIDE the tenant's transaction, so another tenant's are simply not found.
  Every row carries its source code and link (or the visible ``no_source`` state).
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.tenant_plane import plane_of
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.ports import NotConfigured
from account_intelligence.runs import lifecycle
from account_intelligence.runs.transitions import TERMINAL
from metering import events as usage
from metering import limits
from tenancy import settings
from views import materialize, query, saved
from views.tables import TABLES, specs

router = APIRouter(prefix="/accounts", tags=["accounts"])
register("GET", "/v1/accounts", tenant_route("read_public", "read"))
register("POST", "/v1/accounts", tenant_route("add_account", "run"))
register("GET", "/v1/accounts/{domain}/tables/{table}", tenant_route("read_public", "read"))
register("GET", "/v1/accounts/{domain}", tenant_route("read_public", "read"))
DOMAIN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$"
_TERMINAL = sorted(s.value for s in TERMINAL)


async def exclusion_rule(s: Any) -> settings.ExclusionFilter:
    """The tenant's current exclusions (409 when the stored settings no longer validate)."""
    try:
        return settings.ExclusionFilter.of((await settings.current(s)).settings)
    except settings.SettingsInvalid:
        raise APIProblem(
            409,
            "settings_invalid",
            "Settings need attention",
            "An admin must save the tenant settings again.",
        ) from None


@router.get("", response_model=C.AccountPage)
async def list_accounts(
    request: Request,
    after: str | None = Query(default=None, max_length=253),
    search: str | None = Query(default=None, max_length=200),
    enforce_project_scope: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """The tenant's accounts by domain, each with its latest run (excluded companies hidden)."""
    if search and enforce_project_scope:
        from account_intelligence.chat.service import OutOfContextError, validate_project_context

        try:
            validate_project_context(search, strict=True)
        except OutOfContextError as err:
            raise APIProblem(
                422,
                "out_of_context",
                "Search query out of project context",
                str(err),
            ) from None

    search_pattern = f"%{search.strip()}%" if search and search.strip() else None
    async with tenant_txn(ident.tenant_id) as s:
        rule = await exclusion_rule(s)
        rows = (
            await s.execute(
                text(
                    "SELECT a.id, a.domain, a.name, r.id AS run_id, r.status, r.created_at"
                    " FROM accounts a LEFT JOIN LATERAL (SELECT id, status, created_at"
                    " FROM account_runs WHERE account_id = a.id ORDER BY created_at DESC LIMIT 1)"
                    " r ON true WHERE (CAST(:after AS text) IS NULL OR a.domain > :after)"
                    " AND (CAST(:sp AS text) IS NULL OR a.domain ILIKE :sp OR a.name ILIKE :sp)"
                    " ORDER BY a.domain LIMIT :n"
                ),
                {"after": after, "sp": search_pattern, "n": limit + 1},
            )
        ).all()
        from datetime import UTC, datetime

        from account_intelligence.replica.workspace import account_rows

        extra = {r["domain"]: r for r in await account_rows(s, datetime.now(UTC))}
    page = rows[:limit]
    items = [
        {
            **{k: v for k, v in extra.get(r.domain, {}).items() if k not in ("domain", "name")},
            "id": str(r.id),
            "domain": r.domain,
            "name": r.name,
            "latest_run": None
            if r.run_id is None
            else {
                "run_id": str(r.run_id),
                "status": r.status,
                "created_at": r.created_at.isoformat(),
            },
        }
        for r in page
        if not rule.excludes_company(r.domain)
    ]
    return {"accounts": items, "next_after": page[-1].domain if len(rows) > limit else None}


class AccountBody(BaseModel):
    """An account to add. No tenant field."""

    model_config = ConfigDict(extra="forbid")
    domain: str = Field(pattern=DOMAIN, max_length=253)


@router.post("", response_model=C.AccountAdded, status_code=201)
async def add_account(
    body: AccountBody, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Add an account to the tenant's list."""
    async with tenant_txn(ident.tenant_id) as s:
        rule = await exclusion_rule(s)
        if rule.excludes_company(body.domain):
            raise APIProblem(
                409,
                "excluded_company",
                "Excluded company",
                "This company is on the tenant's exclusion list.",
            )
        account_id, created = await lifecycle.add_account(s, ident.tenant_id, body.domain)
        if created:
            now = plane_of(request).clock.now()
            try:
                await limits.consume(s, "accounts", now)
            except limits.LimitReached as e:
                raise limits.limit_problem(e) from None
            await usage.record(s, "account_added", body.domain, now)
        await audit_action(
            s,
            request,
            audit_class="run",
            action="account_add",
            resource_type="account",
            resource_id=body.domain,
        )
    return {"domain": body.domain, "account_id": str(account_id), "created": created}


@router.get("/{domain}", response_model=C.AccountDetail)
async def read_account(
    domain: str, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """One account of the tenant with its newest runs (status labels as the client sees them).
    An unknown or excluded domain is simply not found."""
    from views.progress import STATUS_LABELS

    async with tenant_txn(ident.tenant_id) as s:
        rule = await exclusion_rule(s)
        if not re.fullmatch(DOMAIN, domain) or rule.excludes_company(domain):
            raise HTTPException(404, "not found")
        acc = (
            await s.execute(
                text("SELECT id, domain, name, created_at FROM accounts WHERE domain = :d"),
                {"d": domain},
            )
        ).first()
        if acc is None:
            raise HTTPException(404, "not found")
        runs = (
            await s.execute(
                text(
                    "SELECT id, status, run_mode, created_at, finished_at FROM account_runs"
                    " WHERE account_id = :a ORDER BY created_at DESC, id DESC LIMIT 20"
                ),
                {"a": acc.id},
            )
        ).all()
    return {
        "domain": acc.domain,
        "name": acc.name,
        "added_at": acc.created_at.isoformat(),
        "runs": [
            {
                "run_id": str(r.id),
                "status": r.status,
                "label": STATUS_LABELS.get(r.status, r.status),
                "mode": r.run_mode,
                "created_at": r.created_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in runs
        ],
    }


def _row(r: Any) -> dict[str, Any]:
    return {
        "position": int(r.position),
        "ref": r.ref,
        "row_id": r.row_id,
        "cells": dict(r.cells),
        "source": {"code": r.source_code, "url": r.source_url, "state": r.source_state},
    }


async def resolve_run(
    s: Any, domain: str, run_id: UUID | None, rule: settings.ExclusionFilter
) -> tuple[UUID, UUID]:
    """(account id, run id) inside the tenant: the domain must be the tenant's and not excluded;
    the run must be the account's (default: its latest finished run)."""
    if not re.fullmatch(DOMAIN, domain) or rule.excludes_company(domain):
        raise HTTPException(404, "not found")
    account = (
        await s.execute(text("SELECT id FROM accounts WHERE domain = :d"), {"d": domain})
    ).scalar_one_or_none()
    if account is None:
        raise HTTPException(404, "not found")
    found = (
        await s.execute(
            text(
                "SELECT id FROM account_runs WHERE account_id = :a"
                " AND (CAST(:r AS uuid) IS NULL OR id = :r) AND status = ANY(:t)"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"a": account, "r": run_id, "t": _TERMINAL},
        )
    ).scalar_one_or_none()
    if found is None:
        raise APIProblem(
            409,
            "no_finished_run",
            "No finished run",
            "This account has no finished run to show yet.",
        )
    return UUID(str(account)), UUID(str(found))


async def materialized(request: Request, tenant_id: UUID, run_id: UUID) -> materialize.Materialized:
    """The run's materialised tables (built on first use from the export reader)."""
    container = request.app.state.container
    builder = getattr(container, "builder", None)
    if builder is None or getattr(builder, "reader", None) is None:
        raise NotConfigured("the export content reader is not configured")
    try:
        return await materialize.materialize(
            tenant_txn,
            builder.reader,
            builder.cfg,
            builder.grading,
            tenant_id,
            run_id,
            plane_of(request).clock.now(),
        )
    except materialize.NotReady:
        raise APIProblem(
            409, "run_not_finished", "Run not finished", "The tables appear when the run finishes."
        ) from None
    except materialize.UnknownRun:
        raise HTTPException(404, "not found") from None


@router.get("/{domain}/tables/{table}", response_model=C.TablePage)
async def read_table(
    domain: str,
    table: str,
    request: Request,
    spec: str | None = Query(default=None, max_length=4000),
    view_id: UUID | None = Query(default=None),
    cursor: str | None = Query(default=None, max_length=1000),
    run_id: UUID | None = Query(default=None),
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """One page of a client table."""
    if table not in TABLES:
        raise HTTPException(404, "not found")
    tspec = specs()[table]
    async with tenant_txn(ident.tenant_id) as s:
        rule = await exclusion_rule(s)
        _, run = await resolve_run(s, domain, run_id, rule)
        q_spec = query.QuerySpec()
        if view_id is not None:
            try:
                view = await saved.get(s, view_id, user=ident.user_id)
                if view.table != table:
                    raise saved.SavedViewError("wrong_table")
                q_spec = saved.applicable(view, tspec)
            except saved.SavedViewError as e:
                raise APIProblem(
                    409 if e.code == "saved_view_stale" else 404,
                    e.code,
                    "Saved view unavailable",
                    "Columns no longer available: " + ", ".join(e.columns)
                    if e.columns
                    else "The saved view cannot be used.",
                ) from None
        elif spec:
            try:
                q_spec = query.parse(json.loads(spec))
            except (ValueError, query.QueryError):
                raise APIProblem(
                    422, "malformed_query", "Bad query", "The table query is not valid."
                ) from None
    mat = await materialized(request, ident.tenant_id, run)
    try:
        validated = query.validate(q_spec, tspec)
        sql, params = query.compile_query(validated, run_id=str(run), cursor=cursor)
    except query.QueryError as e:
        raise APIProblem(422, e.code, "Bad query", "The table query is not allowed.") from None
    async with tenant_txn(ident.tenant_id) as s:
        rows = (await s.execute(text(sql), params)).all()
    return {
        "run_id": str(run),
        "table": table,
        "materialization": {"status": mat.status, "reason": mat.reason, "counts": mat.counts},
        "columns": [
            {
                "key": c.key,
                "header": c.header,
                "kind": c.kind,
                "sortable": c.sortable,
                "operators": sorted(c.ops),
            }
            for c in tspec.columns
        ],
        "rows": [_row(r) for r in rows[: validated.page_size]],
        "next_cursor": query.next_cursor(list(rows), validated),
    }
