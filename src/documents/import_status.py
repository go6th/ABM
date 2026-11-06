"""The status of an import, per upload and per part (migration 0106, ingestion hardening PR2).

``import_runs`` holds one row per uploaded document (a re-import reuses it and counts the
attempt); ``import_run_parts`` one row per part of the workspace import (jobs, people, tech, docx,
raw) with its status, rows written and skipped, the first three skip reasons and the error class
of a failure. This module is the only writer. Every write runs in its own short transaction
(``txn(tenant_id)``) as the document's owner, so a part that is running, or one that died, is
visible to a screen while the import is still going. Nothing here stores a cell: a reason is a row
number and a rule, an error is a class or constraint name.

Retry (0107, PR3): a failed part gets at most ``MAX_ATTEMPTS`` attempts. After a failure that is
not the last, ``next_attempt_at`` is set ``RETRY_BASE_S`` x 2^(attempts - 1) ahead (5, then 10
minutes); after the last it is cleared and the part stays failed for good. A part found still
``running`` (or ``pending``) long after it started died with its worker: it counts as a spent
attempt. ``due_parts`` hands the sweep only those parts, never a part that succeeded.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import text

PARTS = ("jobs", "people", "tech", "docx", "raw")
MAX_ATTEMPTS = 3
RETRY_BASE_S = 300
# a part still running this long after its last update died with its worker
STALE_RUNNING_S = 1800
MAX_REASONS = 3
_REASON_CHARS = 120
_ERROR_CHARS = 300


def _as(owner: str | None) -> contextlib.AbstractContextManager[Any]:
    """Act as the document's owner for the block (the rows are owned like the document)."""
    if not owner:
        return contextlib.nullcontext()
    from account_intelligence.db.session import acting_as

    return acting_as(owner)


acting_owner = _as  # for the importer's writes to rows owned like the document


def _reasons(reasons: Sequence[str]) -> str:
    import json

    return json.dumps([str(r)[:_REASON_CHARS] for r in reasons[:MAX_REASONS]])


def row_reasons(rows: Sequence[int | None], rule: str) -> list[str]:
    """The first three reasons for skipped rows: ``row 12: <rule>`` (no number when the row's
    position is unknown)."""
    out: list[str] = []
    for n in rows[:MAX_REASONS]:
        out.append(f"row {n}: {rule}" if n is not None else rule)
    return out


def status_of(*, failed: bool, had_input: bool) -> str:
    """The part's status once it has finished: failed if it raised; skipped if it had nothing to
    write; ok otherwise (rows may still have been skipped)."""
    if failed:
        return "failed"
    return "ok" if had_input else "skipped"


async def begin(
    txn: Any,
    tenant_id: UUID,
    *,
    document_id: UUID,
    account_id: UUID | None,
    owner: str | None,
    parts: dict[str, tuple[bool, int, Sequence[str]]],
    only: frozenset[str] | None = None,
) -> UUID:
    """Create (or reuse, attempts + 1) the document's import run, status ``running``, and its
    part rows: ``pending`` for a part that will run, ``skipped`` (rows 0, or the skipped count and
    reasons given) for one with nothing to do. ``parts`` maps a part to (will_run, rows_skipped,
    reasons). With ``only`` (a retry of some parts), the rows of the other parts are left exactly
    as they are, their attempts included. One short transaction, before any part runs."""
    with _as(owner):
        async with txn(tenant_id) as s:
            run_id = (
                await s.execute(
                    text(
                        "INSERT INTO import_runs (tenant_id, owner_user_id, account_id,"
                        " document_id, status, attempts, started_at)"
                        " SELECT d.tenant_id, d.owner_user_id, CAST(:a AS uuid), d.id,"
                        " 'running', 1, now()"
                        " FROM documents d WHERE d.tenant_id = :t AND d.id = :d"
                        " ON CONFLICT (tenant_id, document_id) DO UPDATE SET"
                        " status = 'running', attempts = import_runs.attempts + 1,"
                        " account_id = excluded.account_id, error_message = NULL,"
                        " started_at = now(), finished_at = NULL"
                        " RETURNING id"
                    ),
                    {"t": tenant_id, "d": document_id, "a": account_id},
                )
            ).scalar_one()
            if not parts and only is None:
                # an import with no parts (a loose workbook, or one that failed before its parts
                # were planned): an earlier attempt's part rows no longer describe it
                await s.execute(
                    text(
                        "DELETE FROM import_run_parts WHERE tenant_id = :t AND import_run_id = :r"
                    ),
                    {"t": tenant_id, "r": run_id},
                )
            for part, (will_run, skipped, reasons) in parts.items():
                if only is not None and part not in only:
                    continue
                await s.execute(
                    text(
                        "INSERT INTO import_run_parts (tenant_id, owner_user_id, import_run_id,"
                        " part, status, rows_written, rows_skipped, skip_reasons, error_message,"
                        " attempts, started_at, finished_at, updated_at)"
                        " SELECT r.tenant_id, r.owner_user_id, r.id, CAST(:p AS text),"
                        " CAST(:st AS text),"
                        " 0, CAST(:sk AS integer), CAST(:rs AS jsonb), NULL, 0, NULL,"
                        " CASE WHEN CAST(:st AS text) = 'skipped' THEN now() END, now()"
                        " FROM import_runs r"
                        " WHERE r.tenant_id = :t AND r.id = :r"
                        " ON CONFLICT (tenant_id, import_run_id, part) DO UPDATE SET"
                        " status = excluded.status, rows_written = 0,"
                        " rows_skipped = excluded.rows_skipped,"
                        " skip_reasons = excluded.skip_reasons, error_message = NULL,"
                        " started_at = NULL, finished_at = excluded.finished_at,"
                        " next_attempt_at = NULL, updated_at = now()"
                    ),
                    {
                        "t": tenant_id,
                        "r": run_id,
                        "p": part,
                        "st": "pending" if will_run else "skipped",
                        "sk": skipped,
                        "rs": _reasons(reasons),
                    },
                )
    return UUID(str(run_id))


async def set_file_import_run(
    txn: Any, tenant_id: UUID, import_run_id: UUID, file_import_run_id: UUID, owner: str | None
) -> None:
    """Remember the account run the rows were written under."""
    with _as(owner):
        async with txn(tenant_id) as s:
            await s.execute(
                text(
                    "UPDATE import_runs SET file_import_run_id = :f"
                    " WHERE tenant_id = :t AND id = :r"
                ),
                {"t": tenant_id, "r": import_run_id, "f": file_import_run_id},
            )


async def part_started(
    txn: Any, tenant_id: UUID, import_run_id: UUID, part: str, owner: str | None
) -> None:
    """The part is running now (its own transaction, so a screen sees it)."""
    with _as(owner):
        async with txn(tenant_id) as s:
            await s.execute(
                text(
                    "UPDATE import_run_parts SET status = 'running', attempts = attempts + 1,"
                    " started_at = now(), finished_at = NULL, error_message = NULL,"
                    " updated_at = now()"
                    " WHERE tenant_id = :t AND import_run_id = :r AND part = :p"
                ),
                {"t": tenant_id, "r": import_run_id, "p": part},
            )


async def part_finished(
    txn: Any,
    tenant_id: UUID,
    import_run_id: UUID,
    part: str,
    owner: str | None,
    *,
    status: str,
    rows_written: int,
    rows_skipped: int,
    reasons: Sequence[str],
    error: str | None = None,
) -> None:
    """The part's outcome (its own transaction, after the part's own transaction ended)."""
    with _as(owner):
        async with txn(tenant_id) as s:
            await s.execute(
                text(
                    "UPDATE import_run_parts SET status = :st, rows_written = :w,"
                    " rows_skipped = :sk, skip_reasons = CAST(:rs AS jsonb),"
                    " error_message = :e, finished_at = now(), updated_at = now(),"
                    # a failure that is not the last waits RETRY_BASE_S x 2^(attempts - 1)
                    " next_attempt_at = CASE WHEN :st = 'failed' AND attempts < :max"
                    "   THEN now() + make_interval(secs => :base * power(2, attempts - 1))"
                    " END"
                    " WHERE tenant_id = :t AND import_run_id = :r AND part = :p"
                ),
                {
                    "max": MAX_ATTEMPTS,
                    "base": RETRY_BASE_S,
                    "t": tenant_id,
                    "r": import_run_id,
                    "p": part,
                    "st": status,
                    "w": max(0, int(rows_written)),
                    "sk": max(0, int(rows_skipped)),
                    "rs": _reasons(reasons),
                    "e": error[:_ERROR_CHARS] if error else None,
                },
            )


async def finish_run(
    txn: Any,
    tenant_id: UUID,
    import_run_id: UUID,
    owner: str | None,
    *,
    error: str | None = None,
) -> str:
    """The run's overall status from its parts: failed if any part failed (or ``error`` is given),
    else ok. Returns it."""
    with _as(owner):
        async with txn(tenant_id) as s:
            any_failed = (
                await s.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM import_run_parts p"
                        " WHERE p.tenant_id = :t AND p.import_run_id = :r AND p.status = 'failed')"
                    ),
                    {"t": tenant_id, "r": import_run_id},
                )
            ).scalar_one()
            status = "failed" if (any_failed or error) else "ok"
            if error:
                # the import stopped before or between its parts: the parts that never finished
                # did not run, and say so (not "pending" under a finished run). The sweep tries
                # the whole import again (bounded, ``repo.claim_imports``), not these parts
                await s.execute(
                    text(
                        "UPDATE import_run_parts SET status = 'failed', error_message = :e,"
                        " finished_at = now(), updated_at = now(), next_attempt_at = NULL"
                        " WHERE tenant_id = :t AND import_run_id = :r"
                        " AND status IN ('pending', 'running')"
                    ),
                    {"t": tenant_id, "r": import_run_id, "e": error[:_ERROR_CHARS]},
                )
            await s.execute(
                text(
                    "UPDATE import_runs SET status = :st, finished_at = now(),"
                    " error_message = :e WHERE tenant_id = :t AND id = :r"
                ),
                {
                    "t": tenant_id,
                    "r": import_run_id,
                    "st": status,
                    "e": error[:_ERROR_CHARS] if error else None,
                },
            )
    return status


async def record_loose(
    txn: Any,
    tenant_id: UUID,
    *,
    document_id: UUID,
    account_id: UUID | None,
    owner: str | None,
    error: str | None,
) -> UUID:
    """A loose workbook import: one run with no parts, ok or failed."""
    run_id = await begin(
        txn, tenant_id, document_id=document_id, account_id=account_id, owner=owner, parts={}
    )
    await finish_run(txn, tenant_id, run_id, owner, error=error)
    return run_id


async def attempted(s: Any, tenant_id: UUID, document_id: UUID) -> bool:
    """Whether the document's import was attempted (its import run exists), in the caller's
    transaction."""
    found = await s.execute(
        text("SELECT 1 FROM import_runs WHERE tenant_id = :t AND document_id = :d"),
        {"t": tenant_id, "d": document_id},
    )
    return found.first() is not None


async def due_parts(s: Any, tenant_id: UUID, now: Any, limit: int) -> dict[UUID, frozenset[str]]:
    """Claim the parts the sweep should try again now, by document (the caller's transaction, in
    the system scope of ``tenant_id``): a failed part with attempts left whose wait is over, and a
    part that died while running (or never started) with attempts left. Claimed parts are pushed
    ``STALE_RUNNING_S`` ahead so a second sweep running at the same time skips them (``SKIP
    LOCKED``); the retry itself resets them. A part that succeeded, was skipped or spent its last
    attempt is never returned."""
    rows = await s.execute(
        text(
            "UPDATE import_run_parts p SET next_attempt_at = CAST(:now AS timestamptz)"
            "   + make_interval(secs => :stale)"
            " FROM (SELECT q.tenant_id, q.import_run_id, q.part FROM import_run_parts q"
            "   WHERE q.tenant_id = :t AND q.attempts < :max AND ("
            "     (q.status = 'failed' AND q.next_attempt_at <= CAST(:now AS timestamptz))"
            "     OR (q.status IN ('running', 'pending')"
            "       AND q.updated_at < CAST(:now AS timestamptz) - make_interval(secs => :stale)"
            "       AND coalesce(q.next_attempt_at, '-infinity') <= CAST(:now AS timestamptz)))"
            "   ORDER BY q.updated_at LIMIT :n FOR UPDATE SKIP LOCKED) due"
            " WHERE p.tenant_id = due.tenant_id AND p.import_run_id = due.import_run_id"
            "   AND p.part = due.part"
            " RETURNING p.import_run_id, p.part,"
            "   (SELECT r.document_id FROM import_runs r WHERE r.tenant_id = p.tenant_id"
            "     AND r.id = p.import_run_id) AS document_id"
        ),
        {"t": tenant_id, "now": now, "max": MAX_ATTEMPTS, "stale": STALE_RUNNING_S, "n": limit},
    )
    out: dict[UUID, set[str]] = {}
    for r in rows.all():
        out.setdefault(UUID(str(r.document_id)), set()).add(str(r.part))
    return {d: frozenset(parts) for d, parts in out.items()}


def reasons_from_counts(counts: dict[str, int], labels: dict[str, str]) -> Iterator[str]:
    """Reader skip counters as content-free reasons: ``12 rows: no name`` for each non-zero key."""
    for key, label in labels.items():
        if counts.get(key):
            yield f"{counts[key]} rows: {label}"
