"""Incremental re-research of job postings (owner request 2026-10-29): the one place that decides
what a second research of the same account pulls and which run it carries forward.

A first research pulls the 12-month window. A later research of the same account must not pull
and read those months again: at run creation the account's previous DELIVERED run with a job
pipeline record is looked up (``previous_delivered_run``: same tenant and account, the owner
scoping is the tables' row-level security) and two keys go into the run input:

* ``postings_previous_run``: the run whose stored postings are carried over, none pulled, read
  or classified again (``jobs.pipeline.JobsPipeline.run_pass``);
* ``postings_since``: the feed's first-seen bound, ``max(window start, previous as_of minus
  OVERLAP_DAYS)`` (``incremental_since``). The overlap re-asks for the last days of the previous
  pull so a posting the feed indexed late is not missed; a posting present in both is one row.

Both the research graph's Phase 1 feed call (``agents.production.provider.feed_since``) and the
job pipeline's pull (``jobs.adapters.ConnectorPostingSource.pull_window``) read the bound through
``postings_since`` here, so the two calls share one ledger key and the pull is paid once.
Nothing is ever deleted: a carried posting now outside the window stays stored, bucketed
``out_of_window``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from jobs.window import window_start

# days of the previous pull asked for again (a late-indexed posting is still caught)
OVERLAP_DAYS = 7
KEY_PREVIOUS_RUN = "postings_previous_run"
KEY_SINCE = "postings_since"


def postings_since(run_input: Mapping[str, Any] | None) -> date | None:
    """The feed bound an incremental run asked for (``postings_since`` in the run input), or None
    for a full 12-month pull (a first research, or an unreadable value)."""
    value = (run_input or {}).get(KEY_SINCE)
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def previous_run_id(run_input: Mapping[str, Any] | None) -> str | None:
    """The run whose postings this run carries (``postings_previous_run``), or None."""
    value = (run_input or {}).get(KEY_PREVIOUS_RUN)
    if not value:
        return None
    try:
        return str(UUID(str(value)))
    except ValueError:
        return None


def incremental_since(previous_as_of: date, as_of: date) -> date | None:
    """The delta bound: the previous research's date minus the overlap, never before the current
    window start. None when the previous research is older than the window (a full pull is the
    delta anyway) or not earlier than ``as_of``."""
    start = window_start(as_of)
    if previous_as_of < start or previous_as_of > as_of:
        return None
    return max(start, previous_as_of - timedelta(days=OVERLAP_DAYS))


def incremental_input(previous: tuple[UUID, date] | None, as_of: date) -> dict[str, str]:
    """The run-input keys for a research of an account with ``previous`` = (delivered run, its
    as_of); empty when there is nothing to carry."""
    if previous is None:
        return {}
    since = incremental_since(previous[1], as_of)
    if since is None:
        return {}
    return {KEY_PREVIOUS_RUN: str(previous[0]), KEY_SINCE: since.isoformat()}


async def previous_delivered_run(session: Any, account_id: UUID) -> tuple[UUID, date] | None:
    """The account's newest delivered run that has a job pipeline record: its run id and the
    pipeline's ``as_of``. Tenant-scoped by the session (``tenant_txn``); the account id keeps it to
    this account, and the tables' owner policies keep another owner's runs out."""
    row = (
        await session.execute(
            text(
                "SELECT d.run_id, j.as_of FROM deliverables d"
                " JOIN job_pipeline_runs j ON j.run_id = d.run_id AND j.tenant_id = d.tenant_id"
                " WHERE d.account_id = :a AND j.account_id = :a AND d.run_id IS NOT NULL"
                " ORDER BY d.built_at DESC, d.created_at DESC LIMIT 1"
            ),
            {"a": account_id},
        )
    ).first()
    if row is None:
        return None
    return UUID(str(row.run_id)), row.as_of
