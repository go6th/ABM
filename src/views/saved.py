"""Saved views (VW3): tenant objects holding a validated ``QuerySpec`` (never SQL).

* Saving validates the spec against the table now; edits are conditional on ``version``; a view
  is archived, never deleted. A private view is edited by its owner only (``save_own_view``); a
  shared view by roles with ``edit_shared_views`` (D-61).
* Loading re-validates against the table as it is TODAY: a view that names a column the table
  no longer has, or an operator the column no longer allows, is STALE and is refused (with the
  offending columns), never applied with a clause silently dropped.
* Limits per user and per tenant come from ``config/views.yaml`` (proposal).

GUARANTEE(test_saved_views_validate_and_fail_safe_when_a_column_is_removed)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any
from uuid import UUID

import yaml
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.config import CONFIG_DIR
from identity.roles import allowed
from views.query import QueryError, QuerySpec, validate
from views.tables import TableKind, TableSpec


class SavedViewError(ValueError):
    """A refused saved-view operation; ``code`` is a fixed reason."""

    def __init__(self, code: str, columns: tuple[str, ...] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.columns = columns


@lru_cache
def _limits() -> tuple[int, int]:
    raw = yaml.safe_load((CONFIG_DIR / "views.yaml").read_text())["saved_views"]
    return int(raw["max_per_user"]), int(raw["max_shared_per_tenant"])


@dataclass(frozen=True)
class SavedView:
    """One saved view as stored."""

    id: UUID
    owner: str
    table: TableKind
    name: str
    spec: QuerySpec
    shared: bool
    version: int


def stale_columns(spec: QuerySpec, table: TableSpec) -> tuple[str, ...]:
    """Columns a spec names that the table (or the column's operators) no longer allows."""
    bad: set[str] = set()
    for f in spec.filters:
        col = table.column(f.column)
        if col is None or f.op not in col.ops:
            bad.add(f.column)
    for s in spec.sort:
        col = table.column(s.column)
        if col is None or not col.sortable:
            bad.add(s.column)
    return tuple(sorted(bad))


def _row(r: Any) -> SavedView:
    return SavedView(
        r.id,
        r.owner,
        r.table_kind,
        r.name,
        QuerySpec.model_validate(r.spec),
        r.shared,
        int(r.version),
    )


async def create(
    s: AsyncSession,
    *,
    owner: str,
    role: str,
    table: TableSpec,
    name: str,
    spec: QuerySpec,
    shared: bool,
    now: datetime,
) -> SavedView:
    """Save a new view (validated now; limits enforced under a per-owner lock)."""
    if not allowed(role, "save_own_view") or (shared and not allowed(role, "edit_shared_views")):
        raise SavedViewError("not_allowed")
    try:
        validate(spec, table)
    except QueryError as e:
        raise SavedViewError(e.code) from None
    per_user, per_tenant = _limits()
    await s.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended('views:' || app_tenant()::text, 1))")
    )
    mine, shared_count = (
        await s.execute(
            text(
                "SELECT count(*) FILTER (WHERE owner = :o), count(*) FILTER (WHERE shared)"
                " FROM saved_views WHERE archived_at IS NULL"
            ),
            {"o": owner},
        )
    ).one()
    if mine >= per_user or (shared and shared_count >= per_tenant):
        raise SavedViewError("limit_reached")
    row = (
        await s.execute(
            text(
                "INSERT INTO saved_views (owner, table_kind, name, spec, shared, created_at,"
                " updated_at) VALUES (:o, :t, :n, CAST(:s AS jsonb), :sh, :now, :now)"
                " RETURNING id, owner, table_kind, name, spec, shared, version"
            ),
            {
                "o": owner,
                "t": table.kind,
                "n": name,
                "s": spec.model_dump_json(),
                "sh": shared,
                "now": now,
            },
        )
    ).one()
    return _row(row)


async def get(s: AsyncSession, view_id: UUID, *, user: str) -> SavedView:
    """A live view the user may see (their own, or shared)."""
    row = (
        await s.execute(
            text(
                "SELECT id, owner, table_kind, name, spec, shared, version FROM saved_views"
                " WHERE id = :i AND archived_at IS NULL AND (owner = :u OR shared)"
            ),
            {"i": view_id, "u": user},
        )
    ).first()
    if row is None:
        raise SavedViewError("not_found")
    return _row(row)


async def listing(s: AsyncSession, *, user: str, table: str | None = None) -> list[SavedView]:
    """The user's own and the tenant's shared views (bounded)."""
    rows = (
        await s.execute(
            text(
                "SELECT id, owner, table_kind, name, spec, shared, version FROM saved_views"
                " WHERE archived_at IS NULL AND (owner = :u OR shared)"
                " AND (CAST(:t AS text) IS NULL OR table_kind = :t) ORDER BY name LIMIT 300"
            ),
            {"u": user, "t": table},
        )
    ).all()
    return [_row(r) for r in rows]


def _may_edit(view: SavedView, user: str, role: str) -> bool:
    if view.shared:
        return allowed(role, "edit_shared_views")
    return allowed(role, "save_own_view", owner=view.owner == user)


async def update(
    s: AsyncSession,
    view_id: UUID,
    *,
    user: str,
    role: str,
    table: TableSpec,
    expected_version: int,
    now: datetime,
    name: str | None = None,
    spec: QuerySpec | None = None,
    shared: bool | None = None,
    archive: bool = False,
) -> SavedView:
    """Edit (or archive) a view at the version it was read."""
    view = await get(s, view_id, user=user)
    if not _may_edit(view, user, role) or (shared and not allowed(role, "edit_shared_views")):
        raise SavedViewError("not_allowed")
    if spec is not None:
        try:
            validate(spec, table)
        except QueryError as e:
            raise SavedViewError(e.code) from None
    row = (
        await s.execute(
            text(
                "UPDATE saved_views SET name = coalesce(:n, name),"
                " spec = coalesce(CAST(:s AS jsonb), spec), shared = coalesce(:sh, shared),"
                " archived_at = CASE WHEN :arch THEN :now ELSE archived_at END,"
                " version = version + 1, updated_at = :now"
                " WHERE id = :i AND version = :v AND archived_at IS NULL"
                " RETURNING id, owner, table_kind, name, spec, shared, version"
            ),
            {
                "n": name,
                "s": spec.model_dump_json() if spec else None,
                "sh": shared,
                "arch": archive,
                "now": now,
                "i": view_id,
                "v": expected_version,
            },
        )
    ).first()
    if row is None:
        raise SavedViewError("version_conflict")
    return _row(row)


def applicable(view: SavedView, table: TableSpec) -> QuerySpec:
    """The view's spec, re-validated against the table as it is now; SavedViewError
    ``saved_view_stale`` (with the columns) when it no longer fits."""
    stale = stale_columns(view.spec, table)
    if stale:
        raise SavedViewError("saved_view_stale", stale)
    try:
        validate(view.spec, table)
    except QueryError as e:
        raise SavedViewError(e.code) from None
    return view.spec


def as_json(view: SavedView) -> dict[str, Any]:
    """Client JSON of a saved view (no tenant field)."""
    return {
        "id": str(view.id),
        "owner": view.owner,
        "table": view.table,
        "name": view.name,
        "spec": json.loads(view.spec.model_dump_json()),
        "shared": view.shared,
        "version": view.version,
    }
