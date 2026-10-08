"""The Pull agent (Phase 1): code only, no model, no interpretation (G5, guide S9).

For each coverage row in plan order it issues the row's phrasings (built in code from the
checklist label) through the provider port, which is the connector ledger: routed order, pull
once, credit gate. Every successful call's raw body is stored, including zero-result responses,
so the manifest row carries payload references, query records, call references and cost. It never
reads, summarises, grades or drops content.

Outcomes per row (visible, never silently absent, G1):

* a people row without valid consent: Blocked ``consent_required`` with no call made (G8);
* the credit gate refuses a call: attended runs raise ``SpendPause`` (the graph pauses for
  approval and re-runs the node; earlier calls replay free from the ledger); unattended runs, and
  attended runs whose approval was declined (``record_refusals``), mark the row Blocked
  ``spend_not_approved`` when nothing was stored for it; free calls continue (G7);
* every call failed: Blocked with the provider's reason;
* otherwise stored.

Internal history (D-98) runs only when ``internal_history_allowed``; its payloads are private.

GUARANTEE(test_g5_pull_layer_has_no_model_and_no_interpretive_output)
GUARANTEE(test_g8_people_rows_blocked_without_consent)
GUARANTEE(test_kill_mid_pull_and_resume_spends_once)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from agents.config import PlanRow, SkillConfig
from agents.contracts import AgentContract, BudgetMeter
from agents.errors import AgentError
from agents.manifest import ManifestRow, QueryRecord
from agents.ports import (
    CapPause,
    Clock,
    InternalSourcePort,
    PayloadPort,
    ProviderFailed,
    ProviderPort,
    RunContext,
)


class SpendPause(AgentError):
    """The credit gate refused a paid call in an attended run: pause for approval (G7)."""

    code = "spend_pause"

    def __init__(self, row_key: str, reason: str) -> None:
        self.row_key = row_key
        self.reason = reason
        super().__init__(f"{row_key}: {reason}")


def phrasings_for(cfg: SkillConfig, row: PlanRow, ctx: RunContext, year: int) -> tuple[str, ...]:
    """The row's queries from the configured phrasings (code-built, never model text)."""
    topic = cfg.row_labels[row.row_key]
    return tuple(
        p.format(account=ctx.account_name, domain=ctx.domain, topic=topic, year=year)
        for p in cfg.phrasings
    )


@dataclass
class PullAgent:
    """See the module docstring."""

    contract: AgentContract
    cfg: SkillConfig
    provider: ProviderPort
    payloads: PayloadPort
    clock: Clock
    internal: InternalSourcePort | None = None
    record_refusals: bool = field(default=False)

    async def pull_row(
        self,
        ctx: RunContext,
        row: PlanRow,
        meter: BudgetMeter,
        *,
        consent_problem: str | None,
        internal_history: bool,
    ) -> ManifestRow:
        """Pull one row (see the module docstring)."""
        now = self.clock.now()
        if row.people and consent_problem is not None:
            return self.blocked(row, "consent_required")
        refs: list[str] = []
        calls: list[str] = []
        queries: list[QueryRecord] = []
        cost = 0
        failure: str | None = None
        for query in phrasings_for(self.cfg, row, ctx, now.year):
            meter.charge_call()
            try:
                result = await self.provider.call(ctx, row.need, query)
            except CapPause as pause:
                if ctx.attended and not self.record_refusals:
                    raise SpendPause(row.row_key, pause.reason) from pause
                failure = "spend_not_approved"
                break
            except ProviderFailed as error:
                failure = error.reason
                continue
            refs.append(
                await self.payloads.put(ctx, row.row_key, result.url, result.body, "public")
            )
            calls.append(result.call_ref)
            queries.append(
                QueryRecord(
                    text=query, tool_category=result.tool_category, result_count=result.result_count
                )
            )
            cost += result.cost_micros
        if row.internal_history and internal_history and self.internal is not None:
            for query in phrasings_for(self.cfg, row, ctx, now.year)[:1]:
                meter.charge_call()
                result = await self.internal.search(ctx, row.row_key, query)
                refs.append(
                    await self.payloads.put(ctx, row.row_key, result.url, result.body, "private")
                )
                calls.append(result.call_ref)
                queries.append(
                    QueryRecord(
                        text=query,
                        tool_category=result.tool_category,
                        result_count=result.result_count,
                    )
                )
        if not refs:
            return self.blocked(row, failure or "no_calls_succeeded")
        return ManifestRow(
            row_key=row.row_key,
            status="stored",
            payload_refs=tuple(refs),
            call_refs=tuple(calls),
            queries=tuple(queries),
            cost_micros=cost,
            pulled_at=now,
        )

    def blocked(self, row: PlanRow, reason: str) -> ManifestRow:
        """A Blocked manifest row with a reason code."""
        code = re.sub(r"[^a-z_]+", "_", reason.lower()).strip("_") or "provider_failed"
        return ManifestRow(
            row_key=row.row_key,
            status="blocked",
            pulled_at=self.clock.now(),
            blocked_reason=code,
        )
