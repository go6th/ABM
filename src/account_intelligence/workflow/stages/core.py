"""The non-research stages: ``setup`` (1), ``jobs`` (5), ``build`` (11), ``gate`` (12) and
``deliver`` (13).

* ``setup`` proposes the run's credit cap (``spend``, PRD step 1) and records whether approval
  is required: the tenant policy may auto-approve within the run's created budget; otherwise the
  engine's approval gate interrupts the graph (AWAITING_APPROVAL, 24 hour timeout). It resolves
  the careers URL BY VISITING IT (candidate URLs only from a search restricted to the account's
  own domain, stored as verified only after a successful read of exactly that URL) once the cap
  is approved: in setup when auto-approved, otherwise first thing after the approval.
* ``jobs`` runs ``jobs.stage.run_jobs`` over the connector-backed adapters.
* ``build`` and ``gate`` call ``BuilderPort`` and ``GatePort``. A missing adapter
  (``NotConfigured``) ends the run PARTIAL ``builders_not_configured``; a set that failed its
  checks (``BuildIncomplete``) ends it PARTIAL ``build_failed``; never COMPLETED with nothing
  built.
* ``deliver`` writes the delivery note once (what was found, what is open; spend is internal,
  D-30), runs it through the client-output scanner (W7) and stores it by reference.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import text

from account_intelligence.connectors.base import Operation
from account_intelligence.connectors.errors import ConnectorError, RetryableProviderError
from account_intelligence.connectors.models import ReadPageArgs, WebPageRecord, WebSearchArgs
from account_intelligence.connectors.service import call_operation
from account_intelligence.platform.ports import NotConfigured
from account_intelligence.safety.client_output import assert_client_safe
from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.ports import BuildIncomplete
from account_intelligence.workflow.state import RunState

CAREER_HINTS = ("career", "jobs", "join")


def _on_domain(url: str, domain: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == domain or host.endswith("." + domain)


async def _call(deps: RunDeps, op: Operation, args: Any) -> Any:
    if deps.connectors is None:
        return None
    try:
        return await call_operation(op, args, deps.call_context(), deps.connectors)
    except RetryableProviderError as error:
        if (error.attempt or 1) < deps.retry_max_attempts:
            raise
        return None
    except ConnectorError:
        return None


async def verify_careers(deps: RunDeps) -> str:
    """Resolve the careers URL by visiting it (paid calls: only under an approved cap)."""
    if deps.connectors is None:
        return "not_available_yet"
    found = await _call(
        deps,
        Operation.WEB_SEARCH,
        WebSearchArgs(queries=(f"{deps.domain} careers",), include_domains=(deps.domain,)),
    )
    candidates = [
        r.url
        for r in (found.records if found else ())
        if isinstance(r, WebPageRecord)
        and _on_domain(r.url, deps.domain)
        and any(h in r.url.lower() for h in CAREER_HINTS)
    ]
    careers = "not_found"
    for url in candidates[:2]:
        page = await _call(deps, Operation.READ_PAGE, ReadPageArgs(url=url))
        if page is not None and page.records:
            from datetime import UTC, datetime

            from datamodel.careers import ConstructedUrl, Visit, record_visit

            fetch_ref = (page.call_refs or page.raw_refs or ("",))[0]
            try:
                async with deps.txn(deps.tenant_id) as session:
                    await record_visit(
                        session,
                        account_id=deps.account_id,
                        domain=deps.domain,
                        visit=Visit(url, fetch_ref, datetime.now(UTC)),
                    )
            except ConstructedUrl:
                continue
            host = (urlsplit(url).hostname or "").lower()
            deps.extra_allowed_hosts = deps.extra_allowed_hosts | {host}
            careers = "verified"
            break
    return careers


async def setup(deps: RunDeps, state: RunState) -> dict[str, Any]:
    """Step 1 (see the module docstring). The credit cap is proposed first (no paid call before
    an approved cap, K2); the careers lookup runs now only when the tenant policy approved the
    cap, otherwise right after the approval (conflict C12)."""
    from account_intelligence.workflow.stages.spend import propose_and_gate

    gate = await propose_and_gate(deps)
    careers = await verify_careers(deps) if gate.approved else "deferred_until_approval"
    return {
        "coverage_reports": {},
        "references": {
            "careers": careers,
            "approval": "auto" if gate.approved else "required",
            "cap": gate.state,
        },
        "counters": {},
        "loop_exits": {"setup": "converged"},
    }


async def jobs_stage(deps: RunDeps, state: RunState) -> dict[str, Any]:
    """Step 5 through the job pipeline: pull once, then at most ``max_gap_passes`` passes over the
    remainder; re-entry replays the latest stored pass and continues after it."""
    from account_intelligence.grading.config import load_grading_config
    from account_intelligence.grading.errors import HostileContent
    from account_intelligence.grading.spans import check_hostile
    from account_intelligence.workflow.proposals import ProposalStore
    from jobs.adapters import (
        ConnectorPageReader,
        ConnectorPostingSource,
        LlmPostingClassifier,
        ProposalSink,
        RunJobsInput,
        RunPayloads,
        TrackerEvidence,
    )
    from jobs.config import load_jobs_config
    from jobs.pipeline import JobsPipeline
    from jobs.repo import PgPostingRepo
    from jobs.stage import run_jobs

    gcfg = load_grading_config()

    def hostile(body: str) -> bool:
        """True when the text echoes a canary or an instruction (the grading check, G14)."""
        try:
            check_hostile(body, gcfg)
        except HostileContent:
            return True
        return False

    async with deps.txn(deps.tenant_id) as session:
        row = (
            await session.execute(
                text(
                    "SELECT r.run_input, r.created_at, a.careers_url, a.careers_url_verified_at"
                    " FROM account_runs r JOIN accounts a ON a.id = r.account_id"
                    " WHERE r.id = :r"
                ),
                {"r": deps.run_id},
            )
        ).one()
    careers_host = (
        (urlsplit(row.careers_url).hostname or "").lower()
        if row.careers_url and row.careers_url_verified_at
        else None
    )
    pipeline = JobsPipeline(
        load_jobs_config(),
        ConnectorPostingSource(deps, careers_host),
        ConnectorPageReader(deps),
        LlmPostingClassifier(deps, "classify_posting", "posting_classifier.v1"),
        LlmPostingClassifier(deps, "second_pass_posting", "posting_second_pass.v1"),
        canary=deps.canary,
        hostile_markers=tuple(gcfg.spans.instruction_markers),
        is_hostile=hostile,
    )
    sink = ProposalSink(ProposalStore(deps.payloads, deps.payload_key))
    result = await run_jobs(
        str(deps.run_id),
        pipeline,
        PgPostingRepo(deps.txn, deps.tenant_id, deps.account_id),
        RunPayloads(deps),
        TrackerEvidence(deps),
        sink,
        RunJobsInput(
            frozenset((row.run_input or {}).get("lanes_off") or ()), row.created_at.date()
        ),
    )
    async with deps.txn(deps.tenant_id) as session:
        rows = await deps.tracker(session).repo.get_all_rows(deps.run_id)
    owned = {
        r.key: r
        for r in rows
        if r.key in deps.spec.coverage_owners and deps.spec.coverage_owners[r.key] == "jobs"
    }
    references = {"proposals:jobs": sink.reference} if sink.reference else {}
    return {
        "coverage_reports": {
            k: {"status": r.status.value if r.status else "unset"} for k, r in owned.items()
        },
        "references": references,
        "counters": {"tool_calls": result.calls_made, "postings": len(result.postings)},
        "loop_exits": {f"jobs_{k}": v for k, v in result.exit_reasons.items()},
    }


async def build(deps: RunDeps, state: RunState) -> dict[str, Any]:
    """Step 11: the builder port; ``NotConfigured`` ends the run PARTIAL with the reason."""
    if deps.builder is None:
        return {
            "coverage_reports": {},
            "references": {"build": "builders_not_configured"},
            "loop_exits": {"build": "no_progress"},
        }
    try:
        files = await deps.builder.build(str(deps.tenant_id), str(deps.run_id))
    except NotConfigured:
        return {
            "coverage_reports": {},
            "references": {"build": "builders_not_configured"},
            "loop_exits": {"build": "no_progress"},
        }
    except BuildIncomplete as failed:
        return {
            "coverage_reports": {},
            "references": {"build": failed.reason},
            "loop_exits": {"build": "no_progress"},
        }
    return {
        "coverage_reports": {},
        "references": {"build": "built"},
        "counters": {"files_built": len(files)},
        "loop_exits": {"build": "converged"},
    }


async def gate(deps: RunDeps, state: RunState) -> dict[str, Any]:
    """Step 12: the quality gate, at most ``gate_cycles`` fix, rebuild, rerun cycles."""
    if deps.gate is None or (state.get("references") or {}).get("build") != "built":
        return {
            "coverage_reports": {},
            "references": {"gate": "not_run"},
            "loop_exits": {"gate": "no_progress"},
        }
    cycles = int(deps.spec.settings.get("gate_cycles", 3))
    failing: dict[str, list[str]] = {}
    # LOOP(gate fix loop): cap=gate_cycles (3) build, gate, rebuild; exit=converged (passed),
    # cap_reached (PARTIAL with failing checks)
    for _ in range(cycles):
        failing = await deps.gate.check(str(deps.tenant_id), str(deps.run_id))
        if not any(failing.values()):
            return {
                "coverage_reports": {},
                "references": {"gate": "passed"},
                "loop_exits": {"gate": "converged"},
            }
        if deps.builder is not None:
            try:
                await deps.builder.build(str(deps.tenant_id), str(deps.run_id))
            except (NotConfigured, BuildIncomplete):
                break  # the rebuild failed closed; the failing checks stand
    names = sorted(k for k, v in failing.items() if v)
    return {
        "coverage_reports": {},
        "references": {"gate": "failed:" + ",".join(names)},
        "loop_exits": {"gate": "cap_reached"},
    }


async def deliver(deps: RunDeps, state: RunState) -> dict[str, Any]:
    """Step 13: the delivery note, once, leak-scanned (W7)."""
    async with deps.txn(deps.tenant_id) as session:
        tracker = deps.tracker(session)
        view = await tracker.view(deps.run_id, "client")
        others = await tracker.run_input.other_account_names(deps.tenant_id, deps.run_id)
    refs = state.get("references") or {}
    counters = state.get("counters") or {}
    note = {
        "found": {
            "claims_graded": counters.get("claims_graded", 0),
            "postings": counters.get("postings", 0),
        },
        "corrected_during_checking": {"claims_dropped": counters.get("claims_dropped", 0)},
        "still_open": [r for r in view["rows"] if r["status"] not in ("Done", "None")],
        "coverage": view["tally"],
        # D-30: spend figures are internal (the spend report); the tenant sees categories and
        # call counts in its own view, never in a client file
        "spend": "reported internally; categories and call counts in the tenant spend view",
        "deliverables": refs.get("build", "unknown"),
        "quality_gate": refs.get("gate", "unknown"),
    }
    body = json.dumps(note, sort_keys=True, default=str)
    assert_client_safe([body], others)
    key = deps.payload_key("delivery", "note.json")
    await deps.payloads.put(key, body.encode())
    if refs.get("build") in ("builders_not_configured", "build_failed"):
        reason = str(refs["build"])
    elif str(refs.get("gate", "")).startswith("failed"):
        reason = str(refs["gate"])
    else:
        reason = None
    return {
        "coverage_reports": {},
        "references": {"delivery_note": key, "partial_reason": reason or ""},
        "loop_exits": {"deliver": "converged"},
    }


def not_configured(stage: str) -> Any:
    """A handler for a stage with no production adapter (fails closed)."""

    async def handler(state: RunState) -> dict[str, Any]:
        """Raise NotConfigured: the stage has no production adapter."""
        raise NotConfigured(f"stage {stage} has no production adapter")

    return handler
