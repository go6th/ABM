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

A run is chosen as the previous one only when it is a safe base (review 2026-10-29, findings 2,
7 and 19): the newest research by ``as_of`` (not by when its files were last rebuilt) whose run
ended with results (``BASE_STATUSES``) and has delivered files, whose stored job pass pulled its
whole feed (``exit_reasons.pull == "converged"`` and ``pull_complete``, no ``invariant_error``), and
whose matching inputs (the lanes switched off and the platform lexicon) equal the current ones,
because a carried posting is never matched again. Otherwise the research pulls the full window.

Both the research graph's Phase 1 feed call (``agents.production.provider.feed_since``) and the
job pipeline's pull (``jobs.adapters.ConnectorPostingSource.pull_window``) read the bound through
``postings_since`` here, so the two calls share one ledger key and the pull is paid once.
Nothing is ever deleted: a carried posting now outside the window stays stored, bucketed
``out_of_window``.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from jobs.window import window_start

# days of the previous pull asked for again (a late-indexed posting is still caught)
OVERLAP_DAYS = 7
KEY_PREVIOUS_RUN = "postings_previous_run"
KEY_SINCE = "postings_since"
# the run states whose files may be the base of the next research
BASE_STATUSES = ("COMPLETED", "PARTIAL", "BUDGET_EXCEEDED")
# stored passes looked at, newest first, before giving up (a full pull is always correct)
_CANDIDATES = 20


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


@dataclass(frozen=True)
class Matching:
    """The inputs that decide which platforms a posting names: the lanes switched off for the
    run, the lexicon hash and (for a record that predates the lexicon hash) the config hash."""

    lanes_off: frozenset[str]
    lexicon_hash: str
    config_hash: str


def current_matching(lanes_off: Collection[str] | None) -> Matching:
    """The matching inputs of a research started now with ``lanes_off`` switched off."""
    from jobs.config import load_jobs_config

    cfg = load_jobs_config()
    return Matching(frozenset(lanes_off or ()), cfg.lexicon_hash, cfg.config_hash)


def _stored_lanes_off(lanes_off: Any, not_searched: Any) -> frozenset[str]:
    """The lanes a stored record had switched off: its ``lanes_off`` list, else (a record that
    predates it) the lanes it names as not in scope."""
    if isinstance(lanes_off, list):
        return frozenset(str(x) for x in lanes_off)
    if isinstance(not_searched, dict):
        return frozenset(str(k) for k, why in not_searched.items() if why == "lane_not_in_scope")
    return frozenset()


def _is_base(row: Any, matching: Matching | None) -> bool:
    """True when the stored pass of ``row`` can be carried into a research (see the module
    docstring): its feed pull converged and completed, it holds no invariant error, and it matched
    with the same lanes and lexicon."""
    if (row.pull or "") != "converged" or row.invariant_error:
        return False
    if row.pull_complete is not None and str(row.pull_complete).lower() != "true":
        return False
    if matching is None:
        return True
    if _stored_lanes_off(row.lanes_off, row.lanes_not_searched) != matching.lanes_off:
        return False
    if row.lexicon_hash:
        return bool(row.lexicon_hash == matching.lexicon_hash)
    return bool(row.config_hash == matching.config_hash)


async def previous_delivered_run(
    session: Any, account_id: UUID, matching: Matching | None = None
) -> tuple[UUID, date] | None:
    """The account's newest research that is a safe base for the next one (module docstring): its
    run id and the pipeline's ``as_of``. Ordered by the research (``as_of``, then when its record
    was stored), never by when a file was last rebuilt, so rebuilding an older run's files cannot
    make it the base. Tenant-scoped by the session (``tenant_txn``); the account id keeps it to
    this account, and the tables' owner policies keep another owner's runs out. ``matching``
    (None: not checked) is the current research's lane and lexicon inputs."""
    rows = (
        await session.execute(
            text(
                "SELECT j.run_id, j.as_of, j.config_hash,"
                " j.record_json->'exit_reasons'->>'pull' AS pull,"
                " j.record_json->>'invariant_error' AS invariant_error,"
                " j.record_json->>'pull_complete' AS pull_complete,"
                " j.record_json->>'lexicon_hash' AS lexicon_hash,"
                " j.record_json->'lanes_off' AS lanes_off,"
                " j.record_json->'lanes_not_searched' AS lanes_not_searched"
                " FROM job_pipeline_runs j"
                " JOIN account_runs r ON r.id = j.run_id AND r.tenant_id = j.tenant_id"
                " WHERE j.account_id = :a AND r.account_id = :a AND r.status = ANY(:ok)"
                " AND EXISTS (SELECT 1 FROM deliverables d WHERE d.run_id = j.run_id"
                " AND d.tenant_id = j.tenant_id AND d.account_id = :a)"
                " ORDER BY j.as_of DESC, j.created_at DESC LIMIT :n"
            ),
            {"a": account_id, "ok": list(BASE_STATUSES), "n": _CANDIDATES},
        )
    ).all()
    for row in rows:
        if _is_base(row, matching):
            return UUID(str(row.run_id)), row.as_of
    return None
