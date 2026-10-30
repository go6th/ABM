"""The Pull agent (Phase 1): code only, no model, no interpretation (G5, guide S9).

For each coverage row in plan order it issues the row's phrasings (built in code from the
checklist label) through the provider port, which is the connector ledger: routed order, pull
once, credit gate. Every source item a call returned is stored as one payload; a zero-result call
stores none but its ledger reference is the row's proof (the tracker derives None, not Blocked).
It never reads, summarises, grades or drops content.

Outcomes per row (visible, never silently absent, G1):

* a people row without valid consent: Blocked ``consent_required`` with no call made (G8);
* an internal-history row on a run without internal history switched on: Blocked
  ``no_internal_access`` with no call made (the skill never substitutes the web for it);
* the run reached the credits confirmed for it (``cap_reached``): the row is Blocked
  ``budget_cap`` when nothing was stored for it; the budget gate refuses every later paid call
  without charging (free calls still run); the run never pauses for this, it ends
  BUDGET_EXCEEDED with what it found (credit cap per pull);
* the credit gate refuses a call for another reason: attended runs raise ``SpendPause`` (the
  graph pauses for approval and re-runs the node; earlier calls replay free from the ledger);
  unattended runs, and attended runs whose approval was declined (``record_refusals``), mark the
  row Blocked ``spend_not_approved`` when nothing was stored for it; free calls continue (G7);
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
    ProviderResult,
    RunContext,
    SourceItem,
)
from crosscut.fanout import fan_out_all

# Queries of one row asked side by side, and stored items written side by side. A row's queries
# are independent searches (3 to 15 of them, 3 s each and as many page reads as the search found
# pages), asked one after another they held the row for minutes (speed audit 2026-10-27). What
# bounds the calls in flight is the provider's gate (``agents.production.provider``), the
# connectors' pacing and the credit gate, not these numbers.
QUERIES_IN_FLIGHT = 4
# each store is an object write and an archive row in the database (the worker's pool is 10
# connections for everything), so a few at a time per row
STORES_IN_FLIGHT = 4

# The provider's reason when the run has used the credits confirmed for it.
CREDIT_LIMIT = "cap_reached"
# An internal-history row on a run without the client's mail, notes and files switched on.
NO_INTERNAL_ACCESS = "no_internal_access"


class SpendPause(AgentError):
    """The credit gate refused a paid call in an attended run: pause for approval (G7)."""

    code = "spend_pause"

    def __init__(self, row_key: str, reason: str) -> None:
        self.row_key = row_key
        self.reason = reason
        super().__init__(f"{row_key}: {reason}")


# The searches a research goal adds to each goal row, on top of the row's own queries (one per
# row keeps the extra spend small: a goal on the five goal rows is five more searches).
GOAL_PHRASINGS = ("{account} {goal}",)


def phrasings_for(
    cfg: SkillConfig,
    row: PlanRow,
    ctx: RunContext,
    year: int,
    topic: str | None = None,
    goal: str | None = None,
) -> tuple[str, ...]:
    """The row's queries (code-built, never model text): the row's own queries from the skill
    when it has them, otherwise the generic phrasings around the row's label. A topic refresh
    searches with the user's own topic words (whitespace collapsed, at most 200 characters) in
    the generic phrasings instead. A research ``goal`` (the user's words, whitespace collapsed,
    at most 200 characters) ADDS ``GOAL_PHRASINGS`` after those queries and never replaces them;
    the caller passes it only for the configured goal rows."""
    words = " ".join((topic or "").split())[:200]
    if row.queries and not words:
        own = tuple(
            q.format(account=ctx.account_name, domain=ctx.domain, year=year, prev_year=year - 1)
            for q in row.queries
        )
    else:
        label = words or cfg.row_labels[row.row_key]
        own = tuple(
            p.format(account=ctx.account_name, domain=ctx.domain, topic=label, year=year)
            for p in cfg.phrasings
        )
    aim = " ".join((goal or "").split())[:200]
    if not aim:
        return own
    extra = (p.format(account=ctx.account_name, goal=aim) for p in GOAL_PHRASINGS)
    return own + tuple(q for q in dict.fromkeys(extra) if q not in own)


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
    # set once the run has used its confirmed credits (every later paid call is refused by the
    # budget gate without charging; free calls still run)
    capped: bool = field(default=False)
    # a topic refresh: the user's topic words replace the row label in the search phrasings
    topic: str | None = field(default=None)
    # a research goal (the user's words) and the rows it adds searches to (config
    # ``research_scopes.yaml`` goal_rows); every other row searches only its own queries
    goal: str | None = field(default=None)
    goal_rows: frozenset[str] = field(default_factory=frozenset)

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
        if row.internal_history and not (internal_history and self.internal is not None):
            # the skill: "Do not try to substitute web sources for internal history"; a web
            # search for "prior customer status" bought pages about other companies first,
            # before the rows that matter (audit 2026-10-21)
            return self.blocked(row, NO_INTERNAL_ACCESS)
        refs: list[str] = []
        calls: list[str] = []
        queries: list[QueryRecord] = []
        cost = 0
        failure: str | None = None
        public = (
            ()
            if row.internal_history
            else phrasings_for(
                self.cfg,
                row,
                ctx,
                now.year,
                self.topic,
                self.goal if row.row_key in self.goal_rows else None,
            )
        )

        async def ask(query: str) -> ProviderResult | CapPause | ProviderFailed:
            """One query; a refusal or a failed source is returned, not raised."""
            meter.charge_call()
            try:
                return await self.provider.call(ctx, row.need, query, row.row_key)
            except (CapPause, ProviderFailed) as error:
                return error

        # the queries run side by side and are read back in query order, so what a row stores,
        # and what stops it, is what the one-at-a-time loop stored and stopped on
        for query, result in zip(
            public, await fan_out_all(public, ask, limit=QUERIES_IN_FLIGHT), strict=True
        ):
            if isinstance(result, CapPause):
                if result.reason == CREDIT_LIMIT:
                    self.capped = True
                    failure = "budget_cap"
                    break
                if ctx.attended and not self.record_refusals:
                    raise SpendPause(row.row_key, result.reason) from result
                failure = "spend_not_approved"
                break
            if isinstance(result, ProviderFailed):
                failure = result.reason
                continue
            refs += await self._store(ctx, row, result, "public")
            calls += result.call_refs
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
                refs += await self._store(ctx, row, result, "private")
                calls += result.call_refs
                queries.append(
                    QueryRecord(
                        text=query,
                        tool_category=result.tool_category,
                        result_count=result.result_count,
                    )
                )
        if not calls:
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

    async def _store(
        self, ctx: RunContext, row: PlanRow, result: ProviderResult, visibility: str
    ) -> list[str]:
        """One payload per source item (a zero-result call stores none; its call ref is proof).
        The items are written side by side (each is an object-store write and an archive row) and
        returned in the call's order."""

        async def put(item: SourceItem) -> str:
            return await self.payloads.put(ctx, row.row_key, item, visibility)

        return await fan_out_all(result.items, put, limit=STORES_IN_FLIGHT)

    def blocked(self, row: PlanRow, reason: str) -> ManifestRow:
        """A Blocked manifest row with a reason code."""
        code = re.sub(r"[^a-z_]+", "_", reason.lower()).strip("_") or "provider_failed"
        return ManifestRow(
            row_key=row.row_key,
            status="blocked",
            pulled_at=self.clock.now(),
            blocked_reason=code,
        )
