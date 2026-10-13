"""``POST /v1/runs`` in input mode "file" (called by ``runs.create_run``; not a route itself).

* The documents must belong to this tenant (RLS), be within the per-run limits, be scanned and
  parsed, and belong to one confirmed account. A document whose company was never confirmed
  refuses the run with 409 ``company_confirmation_required`` and the detected candidates: the
  company is never guessed.
* The run researches only the documents: its ledger holds zero-cost document reads, never a
  data broker call.
* ``enrich_public`` (off by default) also creates a linked company-mode run on the same account
  (the existing pipeline: its own cap approval, ledger and budget, at most ``credit_cap``); its
  public results are shown beside the file results, each with its own label.
* Same key and body replays the first run; the start, and an enrichment opt-in, are audited.

GUARANTEE(test_ambiguous_company_blocks_the_run_until_confirmed)
GUARANTEE(test_enrichment_respects_the_credit_cap)
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import text

from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.db.session import tenant_txn
from account_intelligence.runs import lifecycle
from account_intelligence.runs.lifecycle import (
    IdempotencyConflict,
    InsufficientCredits,
    RunRequest,
    RunStateConflict,
)
from documents import repo
from documents.company import combine
from documents.config import load_config
from metering import events as usage
from metering import limits


async def _documents(s: Any, body: Any) -> tuple[Any, list[Any]]:
    """The run's account and documents, checked (see the module docstring)."""
    cfg = load_config()
    ids = list(dict.fromkeys(body.file.document_ids))
    if len(ids) > cfg.upload.max_files_per_run:
        raise APIProblem(
            422,
            "too_many_documents",
            "Too many documents",
            f"A run reads at most {cfg.upload.max_files_per_run} documents.",
        )
    docs = await repo.many(s, ids)
    if len(docs) != len(ids):
        raise APIProblem(
            404, "document_not_found", "Document not found", "A document was not found."
        )
    if sum(int(d.size_bytes or d.declared_bytes) for d in docs) > cfg.upload.max_run_bytes:
        raise APIProblem(
            422,
            "run_too_large",
            "Too much material",
            "The documents together are larger than the per-run limit.",
        )
    bad = [d for d in docs if d.status in ("rejected", "failed")]
    if bad:
        raise APIProblem(
            422,
            "document_failed",
            "A document cannot be used",
            "Remove the documents that were rejected or could not be read.",
        )
    account_id = body.file.account_id
    bound = {d.account_id for d in docs if d.account_id is not None}
    if account_id is not None:
        found = (
            await s.execute(text("SELECT id FROM accounts WHERE id = :a"), {"a": account_id})
        ).first()
        if found is None:
            raise APIProblem(
                404, "account_not_found", "Account not found", "The account was not found."
            )
        if bound - {account_id}:
            raise APIProblem(
                409,
                "documents_other_account",
                "Documents of another account",
                "Some documents belong to another account.",
            )
    elif len(bound) > 1:
        raise APIProblem(
            409,
            "documents_other_account",
            "Documents of several accounts",
            "The documents belong to different accounts; start one run per account.",
        )
    unbound = [d for d in docs if d.account_id is None]
    if unbound and account_id is None:
        decision = combine(d.detected_company for d in docs if d.detected_company)
        raise APIProblem(
            409,
            "company_confirmation_required",
            "Confirm the company",
            "Confirm which company these documents are about before the research starts.",
            extra={"detected": decision},
        )
    if unbound:
        await repo.bind_account(s, [d.id for d in unbound], account_id)
    account = account_id or next(iter(bound))
    return account, docs


async def create_file_run(
    body: Any, request: Request, idem: str, ident: Identity, container: Any
) -> dict[str, Any]:
    """See the module docstring."""
    from account_intelligence.api.routes.runs import _run_input, _tracker_for
    from agents.adapters import run_mode_columns
    from agents.config import skill_config
    from agents.modes import RunMode, plan_run
    from tenancy import settings

    enrich_id = None
    try:
        async with tenant_txn(ident.tenant_id) as s:
            account, docs = await _documents(s, body)
            domain: str = (
                await s.execute(text("SELECT domain FROM accounts WHERE id = :a"), {"a": account})
            ).scalar_one()
            waiting = [d for d in docs if d.status != "indexed"]
            if waiting:
                for d in waiting:
                    if d.status == "parsed":
                        await container.scheduler.enqueue_document(ident.tenant_id, d.id)
                raise APIProblem(
                    409,
                    "documents_not_ready",
                    "Documents still processing",
                    "Wait until every document shows Indexed, then start the research.",
                    extra={"waiting": [str(d.id) for d in waiting]},
                )
            company_input, version = await _run_input(
                s, body.model_copy(update={"domain": domain}), ident, container
            )
            run_input: dict[str, Any] = {
                "input_mode": "file",
                "document_ids": sorted(str(d.id) for d in docs),
                "enrich_public": bool(body.file.enrich_public),
            }
            if body.file.credit_cap is not None:
                run_input["credit_cap"] = round(float(body.file.credit_cap), 2)
            now = container.clock.now()
            tracker = await _tracker_for(container, s)
            created = await lifecycle.create_run(
                s,
                ident.tenant_id,
                RunRequest(domain, "standard", idem, run_input),
                container.limits,
                tracker,
            )
            if not created.replayed:
                plan = plan_run(
                    RunMode.FULL,
                    parent_run_id=None,
                    parent_mode=None,
                    parent_config_version=None,
                    requested_config_version=None,
                    current_config_version=skill_config().config_version,
                )
                await run_mode_columns(s, created.run_id, plan)
                await settings.pin_run(s, created.run_id, version)
                await repo.link_run(s, created.run_id, account, [d.id for d in docs])
                await limits.consume(s, "runs_per_period", now)
                await usage.record(s, "run_started", created.run_id, now)
                if body.file.enrich_public:
                    enrich_id = await _enrichment(
                        s,
                        ident,
                        container,
                        domain,
                        idem,
                        created.run_id,
                        company_input,
                        body.file.credit_cap,
                        tracker,
                        version,
                    )
                    await audit_action(
                        s,
                        request,
                        audit_class="run",
                        action="enrichment_opt_in",
                        resource_type="run",
                        resource_id=created.run_id,
                    )
            await audit_action(
                s,
                request,
                audit_class="run",
                action="start_replay" if created.replayed else "start_file_mode",
                resource_type="run",
                resource_id=created.run_id,
            )
    except limits.LimitReached as e:
        raise limits.limit_problem(e) from None
    except IdempotencyConflict:
        raise HTTPException(409, "idempotency key reused with a different body") from None
    except InsufficientCredits:
        raise HTTPException(402, "insufficient credits") from None
    except RunStateConflict:
        raise HTTPException(409, "a concurrent request created this run; retry") from None
    if not created.replayed:
        await container.scheduler.enqueue_slice(ident.tenant_id, created.run_id, 0)
        if enrich_id is not None:
            await container.scheduler.enqueue_slice(ident.tenant_id, enrich_id, 0)
    out = {"run_id": str(created.run_id), "status": created.status, "replayed": created.replayed}
    return out


async def _enrichment(
    s: Any,
    ident: Identity,
    container: Any,
    domain: str,
    idem: str,
    file_run: Any,
    company_input: dict[str, Any],
    credit_cap: float | None,
    tracker: Any,
    version: int,
) -> Any:
    """The linked company-mode run (the existing pipeline) with the cap as its budget."""
    from agents.adapters import run_mode_columns
    from agents.config import skill_config
    from agents.modes import RunMode, plan_run
    from tenancy import settings

    run_limits = container.limits
    if credit_cap is not None:
        run_limits = replace(run_limits, budget_usd=Decimal(str(round(credit_cap, 2))))
    child_input = {**company_input, "linked_file_run": str(file_run)}
    child = await lifecycle.create_run(
        s,
        ident.tenant_id,
        RunRequest(domain, "standard", f"{idem[:48]}:enrich", child_input),
        run_limits,
        tracker,
    )
    if not child.replayed:
        now = container.clock.now()
        await limits.consume(s, "runs_per_period", now)  # a linked run is a run like any other
        await usage.record(s, "run_started", child.run_id, now)
        plan = plan_run(
            RunMode.FULL,
            parent_run_id=None,
            parent_mode=None,
            parent_config_version=None,
            requested_config_version=None,
            current_config_version=skill_config().config_version,
        )
        await run_mode_columns(s, child.run_id, plan)
        await settings.pin_run(s, child.run_id, version)
    return child.run_id
