"""The connector framework (CN1 to CN4): grants, and one bounded, resumable, read-only fetch pass.

* Grants: a tenant admin names Drive folders, CRM objects and CRM fields (``Object.Field``); only
  kinds the source supports and fields the scope table lists are accepted.
* A pass reads the connection and its grants in a transaction, obtains an access token by
  refresh OUTSIDE any transaction (a revoked grant or a scope change disables the connection),
  then walks the source within the pass bounds:
  - Drive: each admin-named folder (and each item an admin cleared) breadth first; every item's
    ancestry is resolved through the source (``connectors_client.drive``); an allowed file is
    fetched (size capped) and stored under the tenant's ``connector-quarantine`` prefix; anything
    else is recorded off limits and NEVER read;
  - CRM: each granted object, with only the granted and table-listed fields; a field the source
    returns beyond them is dropped.
* Every list, metadata and fetch call is logged (``connector_access_log``), flushed per page.
* Content stays in quarantine (``connector_items``) until the Phase B pipeline takes it; nothing
  here writes to a client-facing table, and no port can write to the source.
* Bounds: pages, items, bytes per item, folder depth, ancestry depth; a pass that hits a bound
  saves a cursor and the next pass resumes from it.

GUARANTEE(test_a_fetch_pass_is_bounded_resumable_logged_and_read_only)
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

from connectors_client import access_log, quarantine
from connectors_client.drive import ancestors_to_resolve, decide
from connectors_client.oauth import ConnectorRefused, access_token, mark_broken
from connectors_client.ports import ConnectorClient, DriveMeta, OAuthClient, SourceError
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
    """Allow one folder, object or field (idempotent while live)."""
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
    holds: list[tuple[str, str, str]] = field(default_factory=list)
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
        for resource, kind, reason in run.holds:
            await quarantine.hold(
                s,
                connection_id=run.connection_id,
                resource_id=resource,
                resource_kind=kind,
                reason=reason,
                now=run.now,
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
    run.holds.clear()
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
        cleared = set(await quarantine.cleared(s, connection_id))
        decided = {
            r.resource_id: r.status
            for r in (
                await s.execute(
                    text(
                        "SELECT resource_id, status FROM connector_quarantine"
                        " WHERE connection_id = :c"
                    ),
                    {"c": connection_id},
                )
            ).all()
        }
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
    if conn.kind == "google_drive":
        folders = frozenset(r.resource_id for r in grants if r.grant_kind == "drive_folder")
        cursor = json.loads(conn.cursor) if conn.cursor else None
        await _drive(
            run,
            result,
            tokens.access_token,
            client,
            objects,
            tenant_id,
            folders,
            cleared,
            decided,
            cursor,
            limits,
            txn,
        )
    elif conn.kind in ("salesforce", "hubspot"):
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


async def _drive(
    run: _Run,
    result: PassResult,
    token: str,
    client: ConnectorClient,
    objects: ObjectStore,
    tenant_id: UUID,
    folders: frozenset[str],
    cleared: set[str],
    decided: dict[str, str],
    cursor: dict[str, Any] | None,
    limits: Any,
    txn: TxnFactory,
) -> None:
    metas: dict[str, DriveMeta | None] = {}
    allowed = folders | frozenset(cleared)

    async def meta(file_id: str) -> DriveMeta | None:
        if file_id not in metas:
            try:
                metas[file_id] = await client.file_meta(token, file_id)
                run.log("metadata", "ok" if metas[file_id] else "denied", file_id)
            except SourceError:
                metas[file_id] = None
                run.log("metadata", "error", file_id)
        return metas[file_id]

    async def resolve(start: DriveMeta) -> None:
        frontier, depth = [start], 0
        # LOOP(drive ancestry resolution): cap=max_ancestry_depth levels; exit=no unresolved
        # parent, depth cap (the decision is then unknown ancestry: deny)
        while frontier and depth < limits.max_ancestry_depth:
            nxt: list[DriveMeta] = []
            for node in frontier:
                for parent in ancestors_to_resolve(node):
                    if parent in metas or parent in allowed:
                        continue
                    got = await meta(parent)
                    if got is not None:
                        nxt.append(got)
            frontier, depth = nxt, depth + 1

    queue: list[tuple[str, int, str | None]] = (
        [(str(x[0]), int(x[1]), x[2]) for x in cursor["queue"]]
        if cursor
        else [(f, 0, None) for f in sorted(allowed)]
    )
    seen: set[str] = set()
    # LOOP(drive fetch pass): cap=max_pages_per_pass pages and max_items_per_pass items per pass;
    # exit=complete, page_cap or item_cap (a cursor is saved and the next pass resumes)
    while queue:
        folder, depth, page_token = queue.pop(0)
        if folder in seen and page_token is None:
            continue
        seen.add(folder)
        if result.pages >= limits.max_pages_per_pass or (
            result.fetched + result.held >= limits.max_items_per_pass
        ):
            queue.insert(0, (folder, depth, page_token))
            result.exit_reason = (
                "page_cap" if result.pages >= limits.max_pages_per_pass else "item_cap"
            )
            run.next_cursor = json.dumps({"queue": queue})
            return
        if folder not in allowed:
            folder_meta = await meta(folder)
            if (
                folder_meta is None
                or not decide(folder, metas, allowed, limits.max_ancestry_depth).allowed
            ):
                continue
        try:
            page = await client.list_folder(token, folder, page_token)
            run.log("list", "ok", folder)
        except SourceError:
            run.log("list", "error", folder)
            continue
        result.pages += 1
        if page.next_token:
            queue.insert(0, (folder, depth, page.next_token))
        for item in page.items:
            assert isinstance(item, DriveMeta)
            metas[item.id] = item
            if decided.get(item.id) in ("held", "refused"):
                result.skipped += 1
                continue
            await resolve(item)
            verdict = decide(item.id, metas, allowed, limits.max_ancestry_depth)
            if not verdict.allowed and item.id not in cleared:
                kind = (
                    "shortcut" if item.shortcut_target else ("folder" if item.is_folder else "file")
                )
                run.holds.append((item.id, kind, verdict.reason or "outside_allow_list"))
                decided[item.id] = "held"
                run.log("metadata", "held", item.id)
                result.held += 1
                continue
            if item.is_folder:
                if depth + 1 <= limits.max_folder_depth:
                    queue.append((item.id, depth + 1, None))
                continue
            target = item.shortcut_target or item.id
            try:
                body = await client.fetch(token, target, limits.max_bytes_per_item)
                run.log("fetch", "ok", target)
            except SourceError as e:
                run.log("fetch", "error", target)
                if e.code == "too_large":
                    result.skipped += 1
                continue
            await _store(run, objects, tenant_id, target, body)
            result.fetched += 1
        await _flush(txn, tenant_id, run)
    run.next_cursor = None


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


async def store_matched_item(
    s: Any,
    *,
    tenant_id: UUID,
    connection_id: UUID,
    owner_user_id: str,
    account_id: UUID,
    resource_id: str,
    sha256: str,
    kind: str,
    title: str,
    snippet: str,
    when: datetime | None,
    location: str,
    now: datetime,
) -> None:
    """One mail thread or file matched to one of the user's accounts (Mail and drive): its
    metadata only, private to its owner. A repeated sync of the same item changes nothing."""
    await s.execute(
        text(
            "INSERT INTO connector_items (tenant_id, connection_id, resource_id, sha256,"
            " object_key, bytes, state, fetched_at, owner_user_id, account_id, item_kind, title,"
            " snippet, item_date, location) VALUES (:t, :c, :r, :sha, :k, 0, 'matched', :now,"
            " :u, :a, :kind, :ti, :sn, :d, :loc)"
            " ON CONFLICT (tenant_id, connection_id, resource_id, sha256) DO NOTHING"
        ),
        {
            "t": tenant_id,
            "c": connection_id,
            "r": resource_id[:500],
            "sha": sha256,
            "k": f"tenants/{tenant_id}/connector-quarantine/{sha256}",
            "now": now,
            "u": owner_user_id,
            "a": account_id,
            "kind": kind,
            "ti": title,
            "sn": snippet or None,
            "d": when,
            "loc": location,
        },
    )


async def mark_synced(s: Any, connection_id: UUID, now: datetime) -> None:
    """Record when a per-user connection was last synced."""
    await s.execute(
        text("UPDATE connector_connections SET last_synced_at = :now WHERE id = :c"),
        {"now": now, "c": connection_id},
    )


async def delete_connection_items(s: Any, connection_id: UUID) -> int:
    """Delete everything a connection synced (Disconnect). Returns how many rows went."""
    gone = await s.execute(
        text("DELETE FROM connector_items WHERE connection_id = :c RETURNING id"),
        {"c": connection_id},
    )
    return len(gone.all())
