"""The model analysis every upload gets by itself (owner's rule 2026-11-03: when a file is
uploaded, a model reads it and adds what it finds to the database and the account's screens).

Once a document bound to an account is indexed (and, for a workspace package, imported row by
row), ``start`` creates the file-mode run of that one document (``documents.graph``: facts from
every passage, the tracker, the written summary, the Internal Only file) and enqueues its first
slice, exactly as ``POST /v1/runs`` in input mode "file" would, minus the request: the same
lifecycle, settings pin, run-mode columns, document link and usage event. The run is keyed
``auto-file:<document id>``, so indexing the same document again replays it instead of paying
twice. A tenant whose run limit is reached gets a log line, not an error. The document service
never fails over this: a start that cannot happen is logged and the document stays indexed.

Off since 2026-11-05 (``runs.start_guard``): an upload cost the owner about 100 USD by starting
this run unasked. The upload is indexed and imported as before; the model reads it only when a
person starts a file run and confirms it twice. ``RESEARCH_AUTOMATIC_STARTS=on`` restores it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.db.session import acting_as, tenant_txn
from account_intelligence.runs import lifecycle
from account_intelligence.runs.lifecycle import (
    IdempotencyConflict,
    InsufficientCredits,
    RunRequest,
    RunStateConflict,
)
from account_intelligence.runs.start_guard import automatic_starts_allowed
from documents import repo
from metering import events as usage
from metering import limits
from obs.logging import log_context, logger

_log = logger(__name__)
KEY_PREFIX = "auto-file:"


def idempotency_key(document_id: UUID) -> str:
    """The run key of a document's own analysis (one per document, replayed on a re-index)."""
    return f"{KEY_PREFIX}{document_id}"


async def _tracker(container: Any, session: Any) -> Any:
    from account_intelligence.grading.config import load_grading_config
    from account_intelligence.tracker.adapters import LedgerCallAdapter, RunInputAdapter
    from account_intelligence.tracker.repo import CoverageRepo
    from account_intelligence.tracker.service import TrackerService

    return TrackerService(
        CoverageRepo(session),
        LedgerCallAdapter(session, load_grading_config().category_by_operation),
        RunInputAdapter(session),
        owners=container.spec.coverage_owners,
    )


async def _expected_tokens(s: Any, document_id: UUID) -> int | None:
    """What the file-mode run of the document would use (input plus output tokens of
    ``documents.estimate``); None when there is no estimate (the guard then never pauses it)."""
    from documents.estimate import estimate

    try:
        shown = await estimate(s, [document_id], False, 0.0)
    except Exception as error:  # noqa: BLE001 - the analysis starts without a guard instead
        _log.warning(
            "documents.analysis_estimate_failed",
            extra={"document_id": str(document_id), "error_class": type(error).__name__},
        )
        return None
    return int(shown["input_tokens"]) + int(shown["output_tokens"]) or None


async def start(container: Any, tenant_id: UUID, document_id: UUID) -> UUID | None:
    """Create and enqueue the document's file-mode run (see the module docstring); the run id,
    or None when nothing was started. Every log line of it carries the tenant and document."""
    with log_context(tenant_id=tenant_id, document_id=document_id):
        if not automatic_starts_allowed():
            _log.info(
                "documents.analysis_not_started",
                extra={
                    "tenant_id": str(tenant_id),
                    "document_id": str(document_id),
                    "reason": "automatic_starts_off",
                },
            )
            return None
        return await _start(container, tenant_id, document_id)


async def _start(container: Any, tenant_id: UUID, document_id: UUID) -> UUID | None:
    """The body of ``start``."""
    from agents.adapters import run_mode_columns
    from agents.config import skill_config
    from agents.modes import RunMode, plan_run
    from tenancy import settings

    now: datetime = container.clock.now()
    async with tenant_txn(tenant_id) as s:
        doc = await repo.get(s, document_id)
    if doc is None or doc.status != "indexed" or doc.account_id is None:
        return None
    actor = str(getattr(doc, "uploaded_by", None) or "system")
    account_id = UUID(str(doc.account_id))
    with acting_as(actor):
        async with tenant_txn(tenant_id) as s:
            domain = await s.scalar(
                text("SELECT domain FROM accounts WHERE id = :a AND deleted_at IS NULL"),
                {"a": account_id},
            )
            if domain is None:
                return None
            current = await settings.ensure_saved(s, actor=actor, now=now)
            lanes, _, _ = settings.platform_options()
            run_input: dict[str, Any] = {
                "input_mode": "file",
                "document_ids": [str(document_id)],
                "enrich_public": False,
                "started_by": "upload",
                "lanes_off": sorted(set(lanes) - set(current.settings.platform_lanes)),
                "pursuing_client": current.settings.pursuing_client or None,
            }
            # the pre-run token estimate of a file-mode run (``documents.estimate``): the run
            # pauses and asks at the token guard's multiple of it (``spend_guard.check_tokens``)
            expected = await _expected_tokens(s, document_id)
            if expected:
                run_input["expected_tokens"] = expected
            try:
                created = await lifecycle.create_run(
                    s,
                    tenant_id,
                    RunRequest(str(domain), "standard", idempotency_key(document_id), run_input),
                    container.limits,
                    await _tracker(container, s),
                    # reading the client's own file costs no credits (the file-mode rule)
                    lifecycle.CreditJob(kind="file_analysis", scope="file", actor=actor, credits=0),
                )
            except (IdempotencyConflict, RunStateConflict, InsufficientCredits) as error:
                _log.info(
                    "documents.analysis_not_started",
                    extra={
                        "tenant_id": str(tenant_id),
                        "document_id": str(document_id),
                        "reason": type(error).__name__,
                    },
                )
                return None
            if not created.replayed:
                try:
                    await limits.consume(s, "runs_per_period", now)
                except limits.LimitReached:
                    # the tenant's run limit is reached: the document stays indexed and no
                    # analysis runs; the transaction rolls back the run row with it
                    _log.info(
                        "documents.analysis_not_started",
                        extra={
                            "tenant_id": str(tenant_id),
                            "document_id": str(document_id),
                            "reason": "runs_per_period",
                        },
                    )
                    raise
                plan = plan_run(
                    RunMode.FULL,
                    parent_run_id=None,
                    parent_mode=None,
                    parent_config_version=None,
                    requested_config_version=None,
                    current_config_version=skill_config().config_version,
                )
                await run_mode_columns(s, created.run_id, plan)
                await settings.pin_run(s, created.run_id, current.version)
                await repo.link_run(s, created.run_id, account_id, [document_id])
                await usage.record(s, "run_started", created.run_id, now)
            # the run is this document's own analysis: its settled tokens land on the row
            await repo.mark_analysis_run(s, tenant_id, document_id, created.run_id)
    if not created.replayed:
        await container.scheduler.enqueue_slice(tenant_id, created.run_id, 0)
        _log.info(
            "documents.analysis_started",
            extra={
                "tenant_id": str(tenant_id),
                "document_id": str(document_id),
                "run_id": str(created.run_id),
            },
        )
    return UUID(str(created.run_id))
