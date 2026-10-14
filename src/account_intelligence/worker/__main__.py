"""``python -m account_intelligence.worker``: one run per container, or one nightly check.

* ``--tenant-id T --run-id R``: run one research run (Cloud Run Jobs pass the ids as overridden
  arguments of one execution).
* ``spend-invariants``: check every ledger rule of every tenant (``worker.nightly``).

Exit codes: 0 when the run stopped cleanly (terminal or waiting for a person) or the check found
nothing broken; 1 when a check found a broken rule, or for any other run failure (the platform's
retry policy re-executes; the checkpoint resumes the run); 2 for bad arguments, an invalid
environment or an unknown run; 3 when a check could not run (for example, no database).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID

COMMANDS = ("spend-invariants",)


def _check(command: str) -> int:
    """Run one nightly check with the application's engine."""
    from datetime import UTC, datetime

    from account_intelligence.db.session import SessionLocal, tenant_txn
    from account_intelligence.worker import nightly

    try:
        report = asyncio.run(nightly.spend_invariants(SessionLocal, tenant_txn, datetime.now(UTC)))
    except Exception as exc:  # the check itself could not run: say so, never pass silently
        print(f"{command}: could not run ({type(exc).__name__})", file=sys.stderr)
        return 3
    print(f"{command} {report.line()}")
    for problem in [*map(str, report.runs_failed), *report.credit_problems][:50]:
        print(f"broken {problem}")
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    """Parse the command or the run ids, validate the environment, do the work."""
    from account_intelligence.config import EnvironmentInvalid, load_settings
    from obs.logging import configure_logging

    args_in = list(sys.argv[1:] if argv is None else argv)
    command = args_in[0] if args_in and args_in[0] in COMMANDS else None
    if command is None:
        parser = argparse.ArgumentParser(prog="account_intelligence.worker")
        parser.add_argument("--tenant-id", type=UUID, required=True)
        parser.add_argument("--run-id", type=UUID, required=True)
        try:
            args = parser.parse_args(args_in)
        except SystemExit:
            return 2
    elif len(args_in) > 1:
        print(f"{command} takes no arguments", file=sys.stderr)
        return 2
    try:
        settings = load_settings()
    except EnvironmentInvalid as exc:
        print(str(exc), file=sys.stderr)
        return 2
    configure_logging(settings.log_level, service="worker")
    if command is not None:
        return _check(command)
    from account_intelligence.worker.job import run_job

    try:
        result = asyncio.run(run_job(args.tenant_id, args.run_id))
    except LookupError:
        print("run not found", file=sys.stderr)
        return 2
    print(f"{result.outcome} {result.status} slices={result.slices}")
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
