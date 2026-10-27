"""Read-only views of a run's stored work for the workspace zip: its connector calls and its
sense check.

Why here: the export package may not import the research layers (``agents``, ``connectors``) and
the workspace's pull log has to say what each call asked, what it returned and what it cost. The
ledger already stores all three (``tool_calls``: the call's arguments and record count in
``response``, the dollars charged in ``cost_usd``); this module only reads them and cuts each
call down to what a client may see: the query texts, the domain and the date. Nothing else of
the arguments leaves the database. Pure SQL, no writers (the ledger and the sense-check table keep
their own owners).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text

# The step of a call is the first tracker row (by key) whose proof lists the call; a call no row
# proves has no step. ``jsonb_exists`` is the function form of ``?`` (which the driver reads as a
# bind marker).
_CALLS = (
    "SELECT t.created_at, t.args_hash, t.operation, t.response, t.response_ref,"
    " t.cost_usd, (SELECT min(c.row_key) FROM coverage_rows c WHERE c.run_id = t.run_id"
    " AND jsonb_exists(c.proof -> 'call_refs', t.args_hash)) AS step"
    " FROM tool_calls t WHERE t.run_id = :r AND t.provider_code IS NOT NULL"
    " ORDER BY t.created_at, t.args_hash"
)
_FAILED = (
    "SELECT count(DISTINCT e.args_hash) FROM tool_call_events e WHERE e.run_id = :r"
    " AND e.event = 'failed' AND NOT EXISTS (SELECT 1 FROM tool_calls t"
    " WHERE t.run_id = e.run_id AND t.args_hash = e.args_hash)"
)
_SENSE = (
    "SELECT findings, proposals FROM abm_sense_checks WHERE run_id = :r ORDER BY seq DESC LIMIT 1"
)


@dataclass(frozen=True)
class LedgerCall:
    """One stored connector call, cut down to what the workspace shows. ``response_ref`` is the
    stored payload (read by the caller to list result hosts); it is never shown."""

    at: datetime
    operation: str
    step: str | None
    queries: tuple[str, ...]
    terms: tuple[str, ...]
    domain: str | None
    since: str | None
    result_count: int | None
    cost_usd: float
    response_ref: str | None


@dataclass(frozen=True)
class SenseCheck:
    """The latest stored sense-check result of a run: counts only (there is no free-text store)."""

    findings: int
    proposals: int


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(v).strip() for v in value if isinstance(v, str) and v.strip())


def reduce_args(
    args: Mapping[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...], str | None, str | None]:
    """``(queries, terms, domain, since)`` of a call's recorded arguments. Search queries are
    ``queries``; people title and technology filters are ``terms``; a page call keeps the host of
    its URL, never the path or query string."""
    domain = args.get("domain")
    if not isinstance(domain, str) or not domain:
        url = args.get("url") or args.get("profile_url")
        try:
            domain = (urlsplit(url).hostname or None) if isinstance(url, str) else None
        except ValueError:
            domain = None
    slug = args.get("technology_slug")
    terms = (*_strings(args.get("titles")), *([slug] if isinstance(slug, str) and slug else []))
    since = args.get("since") or args.get("after_date")
    return _strings(args.get("queries")), terms, domain, str(since) if since else None


async def load_ledger_calls(s: Any, *, run_id: UUID) -> list[LedgerCall]:
    """The run's stored calls in time order (ties by call reference), the failed-call count apart
    (``load_failed_call_count``)."""
    out: list[LedgerCall] = []
    for row in (await s.execute(text(_CALLS), {"r": run_id})).all():
        meta = row.response if isinstance(row.response, dict) else {}
        queries, terms, domain, since = reduce_args(meta.get("args") or {})
        count = meta.get("record_count")
        out.append(
            LedgerCall(
                at=row.created_at,
                operation=row.operation or "",
                step=row.step,
                queries=queries,
                terms=terms,
                domain=domain,
                since=since,
                result_count=int(count) if isinstance(count, int) else None,
                cost_usd=float(row.cost_usd or 0),
                response_ref=row.response_ref,
            )
        )
    return out


async def load_failed_call_count(s: Any, *, run_id: UUID) -> int:
    """Calls the run tried that failed and left no stored result."""
    return int((await s.execute(text(_FAILED), {"r": run_id})).scalar_one())


async def load_sense_check(s: Any, *, run_id: UUID) -> SenseCheck | None:
    """The run's latest sense-check counts, if one was stored."""
    row = (await s.execute(text(_SENSE), {"r": run_id})).first()
    return None if row is None else SenseCheck(int(row.findings), int(row.proposals))
