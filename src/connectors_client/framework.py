"""The connector framework (CN1 to CN4): grants, and one bounded, read-only fetch pass.

* Grants: a tenant admin names CRM objects and CRM fields (``Object.Field``); only kinds the
  source supports and fields the scope table lists are accepted.
* A pass reads the connection and its grants in a transaction, obtains an access token by
  refresh OUTSIDE any transaction (a revoked grant or a scope change disables the connection),
  then reads each granted CRM object, with only the granted and table-listed fields; a field the
  source returns beyond them is dropped. Each record is stored under the tenant's
  ``connector-quarantine`` prefix.
* Every list and fetch call is logged (``connector_access_log``), flushed per page.
* Content stays in quarantine (``connector_items``) until the Phase B pipeline takes it; nothing
  here writes to a client-facing table, and no port can write to the source.
* Bounds: pages per pass; a pass that hits the bound ends with ``page_cap``.

GUARANTEE(test_a_fetch_pass_is_bounded_logged_and_read_only)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from connectors_client import access_log
from connectors_client.oauth import ConnectorRefused, access_token, mark_broken
from connectors_client.ports import ConnectorClient, OAuthClient, SourceError
from connectors_client.scopes import allowed_fields, load, spec
from tenancy.ports import ObjectStore, SecretStore
from tenancy.storage import tenant_path

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
_FIELD = "crm_field"


class GrantRefused(ValueError):
    """A grant the source or the scope table does not allow."""


async def add_grant(
    s: Any, *, connection_id: UUID, grant_kind: str, resource_id: str, actor: str, now: datetime
) -> None:
    """Allow one CRM object or field (idempotent while live)."""
    conn = (
        await s.execute(
            text("SELECT kind, status FROM connector_connections WHERE id = :c"),
            {"c": connection_id},
        )
    ).first()
    if conn is None:
        raise GrantRefused("not_found")
    sp = spec(conn.kind)
    if grant_kind not in sp.grant_kinds:
        raise GrantRefused("grant_kind_not_supported")
    if grant_kind == "crm_object" and resource_id not in sp.objects:
        raise GrantRefused("object_not_in_scope_table")
    if grant_kind == _FIELD:
        obj, _, fld = resource_id.partition(".")
        if fld not in sp.objects.get(obj, frozenset()):
            raise GrantRefused("field_not_in_scope_table")
    await s.execute(
        text(
            "INSERT INTO connector_grants (connection_id, grant_kind, resource_id, granted_by,"
            " granted_at) VALUES (:c, :k, :r, :a, :now) ON CONFLICT DO NOTHING"
        ),
        {"c": connection_id, "k": grant_kind, "r": resource_id, "a": actor, "now": now},
    )
    await access_log.log(
        s,
        connection_id=connection_id,
        principal=actor,
        kind=conn.kind,
        purpose="grant",
        outcome="ok",
        at=now,
        resource_id=resource_id,
    )


@dataclass
class PassResult:
    """What one pass did and why it stopped."""

    fetched: int = 0
    held: int = 0
    skipped: int = 0
    pages: int = 0
    exit_reason: str = "complete"


@dataclass
class _Run:
    connection_id: UUID
    kind: str
    principal: str
    now: datetime
    logs: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    next_cursor: str | None = None

    def log(self, purpose: str, outcome: str, resource: str | None = None) -> None:
        self.logs.append({"purpose": purpose, "outcome": outcome, "resource_id": resource})


async def _flush(txn: TxnFactory, tenant_id: UUID, run: _Run) -> None:
    async with txn(tenant_id) as s:
        for entry in run.logs:
            await access_log.log(
                s,
                connection_id=run.connection_id,
                principal=run.principal,
                kind=run.kind,
                at=run.now,
                **entry,
            )
        for item in run.items:
            await s.execute(
                text(
                    "INSERT INTO connector_items (connection_id, resource_id, sha256, object_key,"
                    " bytes, fetched_at) VALUES (:c, :r, :h, :k, :b, :now) ON CONFLICT DO NOTHING"
                ),
                {"c": run.connection_id, **item, "now": run.now},
            )
    run.logs.clear()
    run.items.clear()


async def fetch_pass(
    txn: TxnFactory,
    tenant_id: UUID,
    connection_id: UUID,
    *,
    principal: str,
    oauth: OAuthClient,
    client: ConnectorClient,
    store: SecretStore,
    objects: ObjectStore,
    now: datetime,
) -> PassResult:
    """One bounded pass (see the module docstring)."""
    limits = load()[1]
    async with txn(tenant_id) as s:
        conn = (
            await s.execute(
                text(
                    "SELECT kind, status, scopes, token_ref, cursor FROM connector_connections"
                    " WHERE id = :c"
                ),
                {"c": connection_id},
            )
        ).first()
        if conn is None:
            raise ConnectorRefused("not_found")
        if conn.status != "active" or not conn.token_ref:
            raise ConnectorRefused(f"connection_{conn.status}")
        grants = (
            await s.execute(
                text(
                    "SELECT grant_kind, resource_id FROM connector_grants WHERE connection_id = :c"
                    " AND revoked_at IS NULL"
                ),
                {"c": connection_id},
            )
        ).all()
    run = _Run(connection_id, conn.kind, principal, now)
    result = PassResult()
    try:
        tokens = await access_token(
            oauth,
            store,
            kind=conn.kind,
            token_ref=conn.token_ref,
            granted=tuple(conn.scopes),
            attempts=limits.refresh_attempts,
        )
    except ConnectorRefused as refused:
        run.log("token_refresh", "denied")
        await _flush(txn, tenant_id, run)
        async with txn(tenant_id) as s:
            await mark_broken(s, connection_id, refused.reason)
        result.exit_reason = f"broken:{refused.reason}"
        return result
    run.log("token_refresh", "ok")
    if conn.kind in ("salesforce", "hubspot"):
        objs = {r.resource_id for r in grants if r.grant_kind == "crm_object"}
        flds = {r.resource_id for r in grants if r.grant_kind == _FIELD}
        await _crm(
            run, result, tokens.access_token, client, objects, tenant_id, objs, flds, limits, txn
        )
    else:
        raise ConnectorRefused("connector_not_enabled")
    await _flush(txn, tenant_id, run)
    async with txn(tenant_id) as s:
        await s.execute(
            text(
                "UPDATE connector_connections SET last_fetch_at = :now, cursor = :cur,"
                " version = version + 1 WHERE id = :c"
            ),
            {"now": now, "c": connection_id, "cur": run.next_cursor},
        )
    return result


async def _store(
    run: _Run, objects: ObjectStore, tenant_id: UUID, resource: str, body: bytes
) -> None:
    digest = hashlib.sha256(body).hexdigest()
    key = tenant_path(tenant_id, "connector-quarantine", str(run.connection_id), digest)
    await objects.put_if_absent(key, body, "application/octet-stream")
    run.items.append({"r": resource, "h": digest, "k": key, "b": len(body)})


async def _crm(
    run: _Run,
    result: PassResult,
    token: str,
    client: ConnectorClient,
    objects: ObjectStore,
    tenant_id: UUID,
    objs: set[str],
    flds: set[str],
    limits: Any,
    txn: TxnFactory,
) -> None:
    run.next_cursor = None
    for obj in sorted(objs):
        fields = allowed_fields(run.kind, obj, objs, flds)
        if not fields:
            continue
        page_token: str | None = None
        # LOOP(crm fetch pass): cap=max_pages_per_pass pages per pass; exit=last page, page_cap
        for _ in range(limits.max_pages_per_pass):
            if result.pages >= limits.max_pages_per_pass:
                result.exit_reason = "page_cap"
                return
            try:
                page = await client.list_records(token, obj, fields, page_token)
                run.log("list", "ok", obj)
            except SourceError:
                run.log("list", "error", obj)
                break
            result.pages += 1
            for record in page.items:
                kept = {k: v for k, v in record.fields.items() if k in fields}
                if len(kept) != len(record.fields):
                    run.log("fetch", "denied", f"{obj}/{record.id}")  # extra fields dropped
                body = json.dumps(
                    {"object": obj, "id": record.id, "fields": kept}, sort_keys=True, default=str
                ).encode()
                await _store(run, objects, tenant_id, f"{obj}/{record.id}", body)
                run.log("fetch", "ok", f"{obj}/{record.id}")
                result.fetched += 1
            await _flush(txn, tenant_id, run)
            page_token = page.next_token
            if not page_token:
                break
