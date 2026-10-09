"""``python -m account_intelligence.worker --tenant-id T --run-id R``: one run per container.

Cloud Run Jobs pass the ids as overridden arguments of one execution. Exit codes: 0 when the run
stopped cleanly (terminal or waiting for a person), 2 for bad arguments or an unknown run, 1 for
any other failure (the platform's retry policy re-executes; the checkpoint resumes the run).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from uuid import UUID


def main(argv: list[str] | None = None) -> int:
    """Parse ids, validate the environment, run the job."""
    from account_intelligence.config import EnvironmentInvalid, load_settings
    from account_intelligence.worker.job import run_job
    from obs.logging import configure_logging

    parser = argparse.ArgumentParser(prog="account_intelligence.worker")
    parser.add_argument("--tenant-id", type=UUID, required=True)
    parser.add_argument("--run-id", type=UUID, required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    try:
        settings = load_settings()
    except EnvironmentInvalid as exc:
        print(str(exc), file=sys.stderr)
        return 2
    configure_logging(settings.log_level, service="worker")
    try:
        result = asyncio.run(run_job(args.tenant_id, args.run_id))
    except LookupError:
        print("run not found", file=sys.stderr)
        return 2
    print(f"{result.outcome} {result.status} slices={result.slices}")
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry
    sys.exit(main())
