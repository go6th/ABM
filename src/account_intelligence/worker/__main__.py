"""``python -m account_intelligence.worker``: one run per container, or one nightly check.

* ``--tenant-id T --run-id R``: run one research run (Cloud Run Jobs pass the ids as overridden
  arguments of one execution).
* ``spend-invariants``: check every ledger rule of every tenant (``worker.nightly``).
* ``access-expiry``: warn idle users three days before and end the access of those idle for
  their client's idle days (``replica.access``).
* ``reingest-tables [--workspace T]``: send every indexed document that holds table rows but
  no table store record (ingested before migration 0077) through its read again, so its tables
  can be queried (``documents.tables_reingest``). Idempotent: a document already in the store,
  or already being read, is left alone; run it again after a failure. With ``--workspace``
  only that client's documents are looked at.
* ``reindex [--workspace T]``: send every indexed document through the current read again,
  rebuilding its passages, table store and index from its raw bytes
  (``documents.reindex``). Same idempotency; ``--workspace`` scopes the pass to one client.

Exit codes: 0 when the run stopped cleanly (terminal or waiting for a person) or the check found
nothing broken; 1 when a check found a broken rule, or for any other run failure (the platform's
retry policy re-executes; the checkpoint resumes the run); 2 for bad arguments, an invalid
environment or an unknown run; 3 when a check could not run (for example, no database).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any
from uuid import UUID

COMMANDS = ("spend-invariants", "access-expiry", "reingest-tables", "reindex")
# the document passes may be scoped to one client's workspace (a tenant id)
WORKSPACE_COMMANDS = ("reingest-tables", "reindex")


async def _on_engine(settings: Any, work: Any) -> Any:
    """Bind the Cloud SQL engine in the cloud profiles (as the API lifespan does), run ``work``,
    then close the connector. Locally the configured engine is used as it is."""
    from account_intelligence.db.session import bind_cloud_engine

    connector = await bind_cloud_engine(settings)
    try:
        return await work()
    finally:
        if connector is not None:
            await connector.close_async()


def _check(command: str, settings: Any = None, workspace: UUID | None = None) -> int:
    """Run one nightly check with the application's engine."""
    from datetime import UTC, datetime

    from account_intelligence.config import get_settings
    from account_intelligence.db.session import session_factory, tenant_txn, untenanted_session
    from account_intelligence.worker import nightly

    settings = settings or get_settings()
    if command in WORKSPACE_COMMANDS:
        from account_intelligence.app.container import build_container
        from documents.reindex import reindex_documents
        from documents.tables_reingest import reingest_tables

        requeue = reingest_tables if command == "reingest-tables" else reindex_documents
        try:
            counts = asyncio.run(
                _on_engine(
                    settings,
                    lambda: requeue(
                        untenanted_session,
                        tenant_txn,
                        build_container().scheduler,
                        workspace=workspace,
                    ),
                )
            )
        except Exception as exc:  # the pass itself could not run: say so, never pass silently
            print(f"{command}: could not run ({type(exc).__name__})", file=sys.stderr)
            return 3
        print(
            f"{command} queued={counts['queued']} skipped={counts['skipped']}"
            f" clients={counts['clients']}"
        )
        return 0
    if command == "access-expiry":
        from account_intelligence.replica import access

        try:
            done = asyncio.run(
                _on_engine(
                    settings,
                    lambda: access.expire_everywhere(
                        untenanted_session, tenant_txn, datetime.now(UTC)
                    ),
                )
            )
        except Exception as exc:  # the pass itself could not run: say so, never pass silently
            print(f"{command}: could not run ({type(exc).__name__})", file=sys.stderr)
            return 3
        print(f"{command} {done.line()}")
        return 0 if not done.failed else 1
    try:
        report = asyncio.run(
            _on_engine(
                settings,
                lambda: nightly.spend_invariants(session_factory(), tenant_txn, datetime.now(UTC)),
            )
        )
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
    workspace: UUID | None = None
    if command is None:
        parser = argparse.ArgumentParser(prog="account_intelligence.worker")
        parser.add_argument("--tenant-id", type=UUID, required=True)
        parser.add_argument("--run-id", type=UUID, required=True)
        try:
            args = parser.parse_args(args_in)
        except SystemExit:
            return 2
    else:
        cparser = argparse.ArgumentParser(prog=f"account_intelligence.worker {command}")
        if command in WORKSPACE_COMMANDS:
            cparser.add_argument("--workspace", type=UUID, default=None)
        try:
            cargs = cparser.parse_args(args_in[1:])
        except SystemExit:
            return 2
        workspace = getattr(cargs, "workspace", None)
    try:
        settings = load_settings()
    except EnvironmentInvalid as exc:
        print(str(exc), file=sys.stderr)
        return 2
    configure_logging(settings.log_level, service="worker")
    from account_intelligence.db.session import set_system

    set_system()  # a command is a system job (private workspaces, migration 0063)
    if command is not None:
        return _check(command, settings, workspace)
    from account_intelligence.db.owners import act_as_owner_of
    from account_intelligence.worker.job import run_job

    async def as_owner() -> Any:
        """Run the job as the run's owner (its rows are the owner's)."""
        await act_as_owner_of("run", args.tenant_id, args.run_id)
        return await run_job(args.tenant_id, args.run_id)

    try:
        result = asyncio.run(_on_engine(settings, as_owner))
    except LookupError:
        print("run not found", file=sys.stderr)
        return 2
    print(f"{result.outcome} {result.status} slices={result.slices}")
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
