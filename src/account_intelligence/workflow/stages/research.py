"""The generic research stage (baseline, news, tech, people, name searches, pass 1 research rows).

One pass: (1) the planner (tier ``planner``) proposes typed calls; it sees the account domain, the
open rows and the allowed operations, never any page text; (2) the calls run through
``call_operation`` (``workflow.calls``); (3) the tool-less extractor (tier ``extractor``) turns
the returned records into proposed claims, stored by reference; it carries the canary and an
answer that echoes it is dropped as hostile; (4) every owned row's evidence goes to the tracker,
which derives its status from the ledger.

Gap loop (workflow spec 6): at most ``planner_gap_passes`` re-passes over rows still open; a pass
that moves no row up and adds no call ends the loop with ``no_progress``. Exit reasons are written
to the state. The stage adds no retry of its own (W6).
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from account_intelligence.agents.output import parse_json
from account_intelligence.agents.prompts import prompt
from account_intelligence.grading.adapters import record_text
from account_intelligence.platform.ports import NotConfigured
from account_intelligence.tracker.gap import gap_pass_result
from account_intelligence.tracker.model import Actor, ReasonCode, Status
from account_intelligence.workflow.calls import (
    RowCalls,
    execute,
    operations_for,
    validate_plan,
)
from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.proposals import ProposalStore
from account_intelligence.workflow.spec import StageSpec
from account_intelligence.workflow.state import RunState

OPEN = (None, Status.NOT_DONE, Status.BLOCKED, Status.PARTIAL)
MAX_RECORD_CHARS = 4000
MAX_RECORDS = 40


async def ask_json(deps: RunDeps, task_kind: str, system: str, user: Mapping[str, Any]) -> Any:
    """One model call named by its task kind (R2), with ONE bounded re-ask on unparseable output
    (workflow spec 3.7); the token limit is the task kind's (R7)."""
    body = json.dumps(user, sort_keys=True)
    # LOOP(model re-ask): cap=1 re-ask on unparseable output; exit=parsed, None (item blocked)
    for attempt in range(2):
        note = (
            ""
            if attempt == 0
            else "\nYour previous answer was not valid JSON. Answer with JSON only."
        )
        result = await deps.model(task_kind, system, body + note)
        parsed = parse_json(result.text)
        if parsed is not None:
            return parsed
    return None


class ResearchStage:
    """A stage that plans calls, runs them, extracts claims and reports its rows."""

    def __init__(
        self,
        spec: StageSpec,
        deps: RunDeps,
        labels: Mapping[str, str],
        attribute_prefix: str,
        fixed_reasons: Mapping[str, str] | None = None,
        only_operations: frozenset[str] | None = None,
    ) -> None:
        self.spec = spec
        self.deps = deps
        self.labels = labels
        self.prefix = attribute_prefix
        self.fixed = dict(fixed_reasons or {})  # rows never researched here, with their reason
        self.operations = {
            op: cat
            for op, cat in operations_for(spec.allowed_tools).items()
            if only_operations is None or op in only_operations
        }
        self.proposals = ProposalStore(deps.payloads, deps.payload_key)

    def _limit(self, state: RunState) -> int:
        cap = self.spec.loop_cap or 4
        used = int((state.get("counters") or {}).get("tool_calls", 0))
        overall = int(self.deps.spec.settings.get("max_calls", 60))
        return max(0, min(cap, overall - used))

    async def _plan(self, rows: Sequence[str], limit: int) -> tuple[list[Any], int]:
        if not rows or limit <= 0 or not self.operations:
            return [], 0
        from account_intelligence.connectors.base import Operation
        from account_intelligence.connectors.models import ARGS_FOR

        menu = {op: sorted(ARGS_FOR[Operation(op)].model_fields) for op in sorted(self.operations)}
        plan = await ask_json(
            self.deps,
            "plan_gaps",
            prompt("planner.v1"),
            {
                "account_domain": self.deps.domain,
                "rows": [{"key": r, "label": self.labels[r]} for r in rows],
                "operations": menu,
                "call_limit": limit,
            },
        )
        return validate_plan(plan or {}, rows, self.operations, self.deps.domain, limit)

    async def _extract(self, batch: Mapping[str, RowCalls]) -> list[dict[str, Any]]:
        records = [r for calls in batch.values() for r in calls.records][:MAX_RECORDS]
        if not records:
            return []
        items = []
        for record in records:
            url = getattr(record, "url", None) or record.provenance.source_url
            text = record_text(record)[:MAX_RECORD_CHARS]
            if url and text:
                items.append({"url": url, "text": text})
        if not items:
            return []
        system = prompt("extractor.v1").replace("{canary}", self.deps.canary)
        answer = await ask_json(
            self.deps,
            "extract_facts",
            system,
            {"attribute_prefix": self.prefix, "records": items},
        )
        if answer is None:
            return []  # unparseable twice: nothing proposed (counted by the caller)
        # A canary echo is NOT filtered here: grading's G14 check drops each such claim, flags
        # the source hostile and COUNTS the drop (claim_drops); filtering here would be silent.
        claims = answer.get("claims") if isinstance(answer, dict) else None
        return [c for c in claims if isinstance(c, dict)] if isinstance(claims, list) else []

    async def _report(
        self,
        row_refs: Mapping[str, list[str]],
        batch_rows: Mapping[str, RowCalls],
        rows: Sequence[str],
        budget: bool,
    ) -> None:
        async with self.deps.txn(self.deps.tenant_id) as session:
            tracker = self.deps.tracker(session)
            for row in rows:
                calls = batch_rows.get(row, RowCalls())
                refs = row_refs.get(row, [])
                reason = calls.unattempted or ("budget_cap" if budget else "not_planned")
                await tracker.record_outcome(
                    self.deps.tenant_id,
                    self.deps.run_id,
                    row,
                    refs,
                    Actor.STAGE,
                    None if refs else ReasonCode(reason),
                    stage=self.spec.id,
                    cap_hit=calls.cap_hit,
                )

    async def _rows(self) -> dict[str, Any]:
        async with self.deps.txn(self.deps.tenant_id) as session:
            rows = await self.deps.tracker(session).repo.get_all_rows(self.deps.run_id)
        return {r.key: r for r in rows if r.key in self.spec.coverage}

    async def __call__(self, state: RunState) -> dict[str, Any]:
        researched = [r for r in self.spec.coverage if r not in self.fixed]
        if self.fixed:
            async with self.deps.txn(self.deps.tenant_id) as session:
                tracker = self.deps.tracker(session)
                for row, reason in self.fixed.items():
                    await tracker.record_outcome(
                        self.deps.tenant_id,
                        self.deps.run_id,
                        row,
                        [],
                        Actor.STAGE,
                        ReasonCode(reason),
                        stage=self.spec.id,
                    )
        budget = bool((state.get("counters") or {}).get("budget_exhausted", 0))
        row_refs: dict[str, list[str]] = {r: [] for r in researched}
        proposals: list[dict[str, Any]] = []
        calls_made = refused = 0
        passes = 1 + int(self.deps.spec.settings.get("planner_gap_passes", 2))
        exit_reason = "converged"
        open_rows = list(researched)
        # LOOP(planner gap loop): cap=1 + planner_gap_passes (2); exit=converged, no_progress,
        # budget, cap_reached
        for pass_no in range(passes):
            before = list((await self._rows()).values())
            limit = (
                0
                if budget
                else self._limit(
                    {
                        **state,
                        "counters": {
                            **(state.get("counters") or {}),
                            "tool_calls": int((state.get("counters") or {}).get("tool_calls", 0))
                            + calls_made,
                        },
                    }
                )
            )
            try:
                plan, bad = await self._plan(open_rows, limit)
            except NotConfigured:
                plan, bad = [], 0
            refused += bad
            batch = await execute(self.deps, plan, open_rows)
            if self.deps.connectors is None:
                for row_calls in batch.rows.values():
                    row_calls.unattempted = "not_available_yet"
            budget |= batch.budget_exhausted
            calls_made += batch.calls
            refused += batch.refused
            for row, calls in batch.rows.items():
                row_refs[row].extend(ref for ref in calls.refs if ref not in row_refs[row])
            with contextlib.suppress(NotConfigured):
                proposals.extend(await self._extract(batch.rows))
            await self._report(row_refs, batch.rows, open_rows, budget)
            after = list((await self._rows()).values())
            open_rows = [r.key for r in after if r.key in researched and r.status in OPEN]
            if budget:
                exit_reason = "budget"
                break
            if not open_rows:
                exit_reason = "converged"
                break
            if pass_no > 0 and not gap_pass_result(before, after):
                exit_reason = "no_progress"
                break
            if not plan:
                exit_reason = "no_progress"
                break
        else:
            exit_reason = "cap_reached"
        final = await self._rows()
        references: dict[str, Any] = {}
        if proposals:
            references[f"proposals:{self.spec.id}"] = await self.proposals.put_stage(
                self.spec.id, proposals
            )
        return {
            "coverage_reports": {
                key: {"status": row.status.value if row.status else "unset"}
                for key, row in final.items()
            },
            "references": references,
            "counters": {
                "tool_calls": calls_made,
                "calls_refused": refused,
                "claims_proposed": len(proposals),
                "budget_exhausted": 1
                if budget and not (state.get("counters") or {}).get("budget_exhausted")
                else 0,
            },
            "loop_exits": {self.spec.id: exit_reason},
        }
