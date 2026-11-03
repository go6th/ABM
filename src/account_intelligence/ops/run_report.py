"""``python -m account_intelligence.ops.run_report --tenant T (--domain bd.com | --run R)``

A READ-ONLY report on why a research run ended or stalled, from the run's own records. The
structured logs carry no text (allow-listed fields only), so a stall that logs nothing but
``slice.ended outcome=no_progress`` cannot be explained from them.

It runs with the worker's own database identity, in one tenant transaction in the system scope,
set READ ONLY and rolled back at the end, so it cannot change anything. It prints identifiers,
statuses, counts and timings only: never a name, a passage or a page of the account's data.

Prints, for the latest runs of the account (or the one run): status, the finish reason and stop
detail, slices used, empty slices and the last progress time; then, for the chosen run, the
paid-call events grouped by provider, operation, event and error class (with latency), the
work units by type, and whether the jobs and people pipelines recorded their result.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections.abc import Sequence
from typing import Any, TextIO
from uuid import UUID

from sqlalchemy import text

from account_intelligence.db.session import tenant_txn

DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

_RUNS = text(
    "SELECT r.id, r.status, r.created_at, r.finished_at, r.finished_reason, r.stop_detail,"
    " r.slices_used, r.no_progress_slices, r.progress_at, r.stalled_at, r.run_mode,"
    " r.tool_calls_used, r.spent_usd FROM account_runs r JOIN accounts a ON a.id = r.account_id"
    " WHERE a.domain = :d ORDER BY r.created_at DESC LIMIT :n"
)
_ONE = text(
    "SELECT r.id, r.status, r.created_at, r.finished_at, r.finished_reason, r.stop_detail,"
    " r.slices_used, r.no_progress_slices, r.progress_at, r.stalled_at, r.run_mode,"
    " r.tool_calls_used, r.spent_usd FROM account_runs r WHERE r.id = :r"
)
_CALLS = text(
    "SELECT provider_code, operation, event, coalesce(error_class, '-') AS error_class,"
    " coalesce(cost_source, '-') AS cost_source, count(*) AS n,"
    " round(avg(latency_ms))::int AS avg_ms, max(latency_ms) AS max_ms,"
    " min(created_at) AS first_at, max(created_at) AS last_at"
    " FROM tool_call_events WHERE run_id = :r"
    " GROUP BY 1, 2, 3, 4, 5 ORDER BY n DESC LIMIT 60"
)
_UNITS = text(
    "SELECT unit_type, lane, count(*) AS n FROM work_units WHERE run_id = :r"
    " GROUP BY 1, 2 ORDER BY n DESC LIMIT 40"
)
_PIPES = text(
    "SELECT 'jobs' AS pipeline, created_at FROM job_pipeline_runs WHERE run_id = :r"
    " UNION ALL SELECT 'people', created_at FROM people_pipeline_runs WHERE run_id = :r"
)


def _row(out: TextIO, values: Sequence[Any]) -> None:
    out.write("  " + "  ".join(str(v) for v in values) + "\n")


async def report(tenant: UUID, domain: str | None, run: UUID | None, last: int, out: TextIO) -> int:
    """Print the report; returns the exit code (3 when nothing is found)."""
    from account_intelligence.config import get_settings
    from account_intelligence.db.session import bind_cloud_engine

    connector = await bind_cloud_engine(get_settings())  # staging: the worker's IAM connection
    try:
        return await _report(tenant, domain, run, last, out)
    finally:
        if connector is not None:
            await connector.close_async()


async def _report(
    tenant: UUID, domain: str | None, run: UUID | None, last: int, out: TextIO
) -> int:
    async with tenant_txn(tenant, system=True) as s:
        await s.execute(text("SET TRANSACTION READ ONLY"))
        if run is not None:
            runs = (await s.execute(_ONE, {"r": run})).all()
        else:
            runs = (await s.execute(_RUNS, {"d": domain, "n": last})).all()
        if not runs:
            out.write("no run found\n")
            return 3
        out.write("RUNS (newest first)\n")
        for r in runs:
            out.write(
                f"  {r.id}  {r.status}  mode={r.run_mode}  created {r.created_at:%m-%d %H:%M}"
            )
            out.write(f"  finished {r.finished_at:%m-%d %H:%M}" if r.finished_at else "  open")
            out.write(f"\n    reason={r.finished_reason} detail={r.stop_detail}\n")
            out.write(
                f"    slices={r.slices_used} empty_slices={r.no_progress_slices}"
                f" progress_at={r.progress_at} stalled_at={r.stalled_at}"
                f" calls={r.tool_calls_used} spent=${r.spent_usd}\n"
            )
        chosen = runs[0].id
        out.write(f"\nPAID-CALL EVENTS of run {chosen}\n")
        out.write(
            "  provider  operation  event  error  cost_source  n  avg_ms  max_ms  first..last\n"
        )
        for c in (await s.execute(_CALLS, {"r": chosen})).all():
            _row(
                out,
                [
                    c.provider_code, c.operation, c.event, c.error_class, c.cost_source, c.n,
                    c.avg_ms, c.max_ms, f"{c.first_at:%H:%M}..{c.last_at:%H:%M}",
                ],
            )  # fmt: skip
        out.write("\nWORK UNITS\n")
        for u in (await s.execute(_UNITS, {"r": chosen})).all():
            _row(out, [u.unit_type, u.lane, u.n])
        out.write("\nPIPELINE RESULTS RECORDED\n")
        for p in (await s.execute(_PIPES, {"r": chosen})).all():
            _row(out, [p.pipeline, f"{p.created_at:%m-%d %H:%M}"])
        await s.rollback()
    return 0


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """Parse and run. Exit codes: 0 done, 2 bad arguments, 3 nothing found."""
    parser = argparse.ArgumentParser(prog="python -m account_intelligence.ops.run_report")
    parser.add_argument("--tenant", type=UUID, required=True)
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--domain", help="exact domain of the account, e.g. bd.com")
    which.add_argument("--run", type=UUID, help="one run id")
    parser.add_argument("--last", type=int, default=3, help="how many runs to list")
    try:
        args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit:
        return 2
    if args.domain is not None and not DOMAIN.match(args.domain.strip().lower()):
        sys.stderr.write("--domain must be one plain domain such as bd.com\n")
        return 2
    domain = args.domain.strip().lower() if args.domain else None
    return asyncio.run(
        report(args.tenant, domain, args.run, max(1, min(args.last, 20)), out or sys.stdout)
    )


if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
