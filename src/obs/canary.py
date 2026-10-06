"""Isolation signals (O11) and the sink canary scan (O4).

* ``probe`` (synthetic tenants only): tenant A's session must see none of tenant B's planted rows;
  each result is an insert-only ``canary_probe_results`` row; any leak pages.
* ``scan_texts`` (pure): planted canary strings searched in every sink's captured text (logs,
  spans, metric labels, error reports, run events, operator responses, notification bodies).
* ``rls_denied_total`` counts permission errors on WRITES (SQLSTATE 42501). RLS filters reads
  silently, so this counter is not a leak detector on its own; the prober and the scan are.

GUARANTEE(test_the_prober_detects_a_broken_policy): O11.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
PROBE_TABLES = ("facts", "account_runs", "accounts", "work_unit_events", "llm_calls")


def scan_texts(sinks: Mapping[str, Iterable[str]], canaries: Iterable[str]) -> list[str]:
    """``sink`` names where any canary appears (each hit once)."""
    needles = [c.casefold() for c in canaries if c]
    hits: list[str] = []
    for sink, texts in sinks.items():
        if any(n in t.casefold() for t in texts for n in needles):
            hits.append(sink)
    return hits


async def probe(
    txn: TxnFactory, viewer: UUID, owner: UUID, marker_ids: Mapping[str, Any], now: datetime
) -> list[str]:
    """Tables where ``viewer`` can read ``owner``'s planted row ids (should be none)."""
    leaks: list[str] = []
    async with txn(viewer) as s:
        for table, row_id in sorted(marker_ids.items()):
            if table not in PROBE_TABLES:
                raise ValueError(f"{table} is not a probe table")
            seen: Any = (
                await s.execute(
                    text(f"SELECT count(*) FROM {table} WHERE id = :i"),  # noqa: S608 - allow-listed
                    {"i": row_id},
                )
            ).scalar_one()
            if int(seen):
                leaks.append(table)
        await s.execute(
            text("INSERT INTO canary_probe_results (probe, leaks, at) VALUES ('rls', :n, :at)"),
            {"n": len(leaks), "at": now},
        )
    return leaks


def is_rls_denied(exc: BaseException) -> bool:
    """A permission error from the database (SQLSTATE 42501)."""
    orig = getattr(exc, "orig", exc)
    return (
        getattr(orig, "sqlstate", None) == "42501" or "InsufficientPrivilege" in type(orig).__name__
    )
