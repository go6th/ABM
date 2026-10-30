"""Materialise a finished run's client tables (VW1), once.

The snapshot is read by the export content reader (``exports.readers.pg.PgContentReader``: one
REPEATABLE READ snapshot, public facts as ``public_reader``, the tenant's exclusions applied) and
mapped by ``exports.builder.map_snapshot``, the same function the builders render; the rows go to
``view_rows`` (insert-only, derived) and one ``view_materializations`` row records the counts and
the rows hash. A snapshot the export rules refuse (a forbidden term, a missing mapping) is
materialised as ``withheld`` with no rows: the tables show the reason, and the export would be
refused for the same reason. Two concurrent requests serialise on a per-run advisory lock; the
second finds the first's result.

The exclusion set the rows were filtered with (``exclusion_hash``) is stored with the run. A
read after the tenant changed its exclusions finds a stale materialisation and builds it again:
``reset_view_run`` (a definer function; the application role has no DELETE on these derived
tables) removes the run's old rows and the new ones are inserted in the same transaction (leakage
review 2026-10-30, F1).

GUARANTEE(test_materialisation_is_once_per_run_and_matches_the_mapping)
GUARANTEE(test_an_exclusion_added_after_a_run_reaches_every_surface)
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.grading.config import GradingConfig
from account_intelligence.runs.transitions import TERMINAL
from exports.builder import errors_of, map_snapshot
from exports.config import ExportConfig
from exports.ports import ContentReader
from exports.readers.exclusions import exclusion_hash
from tenancy import settings as tenant_settings
from views.rowsources import rows_of
from views.rowsources.common import ViewRow, rows_hash

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


class NotReady(RuntimeError):
    """The run has not reached a terminal state yet (its tables are progress, not rows)."""


class UnknownRun(LookupError):
    """No such run in the caller's tenant."""


@dataclass(frozen=True)
class Materialized:
    """The materialisation of one run."""

    run_id: UUID
    account_id: UUID
    status: str
    reason: str | None
    counts: dict[str, int]
    rows_hash: str
    exclusion_hash: str | None = None


async def current_exclusion_hash(s: AsyncSession) -> str:
    """The hash of the context tenant's CURRENT exclusion set (``none`` for no exclusions). Stored
    settings that no longer validate give ``invalid``, which no stored file or table carries: the
    files are held back and the page still loads (a build refuses the same settings)."""
    try:
        current = await tenant_settings.current(s)
    except tenant_settings.SettingsInvalid:
        return "invalid"
    return exclusion_hash(tenant_settings.ExclusionFilter.of(current.settings))


async def exclusions_changed_since(s: AsyncSession, at: datetime) -> bool:
    """Whether the tenant's exclusion set is another one than the one in force at ``at`` (the
    latest settings version saved by then; none: no exclusions). A package or file made before
    the change still holds what the tenant excluded since."""
    then = (
        await s.execute(
            text(
                "SELECT settings_json FROM tenant_settings_versions WHERE created_at <= :at"
                " ORDER BY version DESC LIMIT 1"
            ),
            {"at": at},
        )
    ).scalar_one_or_none()
    if then is None:
        before = "none"
    else:
        try:
            before = exclusion_hash(
                tenant_settings.ExclusionFilter.of(
                    tenant_settings.TenantSettings.model_validate(then)
                )
            )
        except ValueError:
            return True
    return before != await current_exclusion_hash(s)


async def _existing(s: AsyncSession, run_id: UUID) -> Materialized | None:
    row: Any = (
        await s.execute(
            text(
                "SELECT run_id, account_id, status, reason, counts, rows_hash, exclusion_hash"
                " FROM view_materializations WHERE run_id = :r"
            ),
            {"r": run_id},
        )
    ).first()
    if row is None:
        return None
    return Materialized(
        row.run_id,
        row.account_id,
        row.status,
        row.reason,
        dict(row.counts or {}),
        row.rows_hash,
        row.exclusion_hash,
    )


def is_current(found: Materialized, current: str) -> bool:
    """Whether a materialisation was filtered with ``current``. One stored before the hash was
    recorded counts as current only when the tenant has no exclusions at all."""
    return (found.exclusion_hash or "none") == current


async def materialize(
    txn: TxnFactory,
    reader: ContentReader,
    cfg: ExportConfig,
    grading: GradingConfig,
    tenant_id: UUID,
    run_id: UUID,
    now: datetime,
) -> Materialized:
    """The run's materialisation, building it on first use."""
    async with txn(tenant_id) as s:
        found = await _existing(s, run_id)
        wanted = await current_exclusion_hash(s)
        if found is not None and is_current(found, wanted):
            return found
        run: Any = (
            await s.execute(
                text("SELECT status, account_id, settings_version FROM account_runs WHERE id = :r"),
                {"r": run_id},
            )
        ).first()
    if run is None:
        raise UnknownRun("run not found")
    if run.status not in TERMINAL:
        raise NotReady("the run has not finished")
    content = await reader.read(str(tenant_id), str(run_id))
    mapped, ctx = map_snapshot(content, cfg, grading)
    refused = bool(errors_of(ctx.violations)) or any(m.model is None for m in mapped.values())
    rows: dict[Any, list[ViewRow]] = {} if refused else dict(rows_of(mapped))
    digest = rows_hash(rows)
    counts = {k: len(v) for k, v in rows.items()}
    async with txn(tenant_id) as s:
        await s.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('views:' || :r, 0))"),
            {"r": str(run_id)},
        )
        found = await _existing(s, run_id)
        if found is not None and is_current(found, wanted):
            return found
        if found is not None:
            # a materialisation made under another exclusion list: replace it whole
            await s.execute(text("SELECT reset_view_run(:r)"), {"r": run_id})
        batch = [
            {
                "run": run_id,
                "acc": run.account_id,
                "kind": r.table,
                "pos": r.position,
                "ref": r.ref,
                "rid": r.row_id,
                "cells": json.dumps(r.cells),
                "search": r.search_text,
                "code": r.source_code if r.source_state == "linked" else None,
                "url": r.source_url if r.source_state == "linked" else None,
                "state": r.source_state,
            }
            for table_rows in rows.values()
            for r in table_rows
        ]
        if batch:
            await s.execute(
                text(
                    "INSERT INTO view_rows (run_id, account_id, table_kind, position, ref, row_id,"
                    " cells, search_text, source_code, source_url, source_state) VALUES (:run,"
                    " :acc, :kind, :pos, :ref, :rid, CAST(:cells AS jsonb), :search, :code, :url,"
                    " :state)"
                ),
                batch,
            )
        status, reason = ("withheld", "export_rules_refused") if refused else ("ready", None)
        await s.execute(
            text(
                "INSERT INTO view_materializations (run_id, account_id, counts, rows_hash,"
                " settings_version, status, reason, created_at, exclusion_hash) VALUES (:r, :a,"
                " CAST(:c AS jsonb), :h, :v, :st, :why, :now, :xh)"
            ),
            {
                "r": run_id,
                "a": run.account_id,
                "c": json.dumps(counts),
                "h": digest,
                "v": run.settings_version,
                "st": status,
                "why": reason,
                "now": now,
                "xh": content.exclusion_hash or "none",
            },
        )
    return Materialized(
        run_id, run.account_id, status, reason, counts, digest, content.exclusion_hash or "none"
    )


async def api_rows(s: AsyncSession, run_id: UUID, table: str) -> list[dict[str, Any]]:
    """Every materialised row of one table, in order (the parity check's API side)."""
    rows = (
        await s.execute(
            text(
                "SELECT cells FROM view_rows WHERE run_id = :r AND table_kind = :k"
                " ORDER BY position"
            ),
            {"r": run_id, "k": table},
        )
    ).all()
    return [dict(r.cells) for r in rows]
