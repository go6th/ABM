"""The hard limits on what one run may spend (owner decision 2026-10-14, Phase 4B section 4).

The owner lost money to runs that repeated paid calls and stalled. Three checks run before every
paid call, in the transaction that starts it:

* **Dollar cap.** A run's real spend (its provider calls on the ledger, ``account_runs.spent_usd``,
  plus its priced model replies in ``model_reply_log``) and the call's estimate may not pass
  ``platform_defaults.run_dollar_cap_usd`` (the admin's setting, default $25, above the per-run
  budget that pauses for approval). The check raises
  ``DollarCapReached``.
* **Repeated calls.** The same provider, operation and normalised query (``query_key``: values
  lower-cased and whitespace collapsed, paging fields left out; first pages only, since a later
  page is bounded by the page limits) may be paid at most ``repeat_call_limit`` times in a run
  (default 3); one more raises ``RepeatedCallStopped``. An identical call is never paid twice in
  the first place (the ledger replays it).
* **Heartbeat.** Every ledger event and model call moves ``account_runs.progress_at``; the sweeper
  marks a run with no progress for ``stall_after_s`` as stalled and tells its owner, who can
  resume it.

* **Token guard** (owner decision 2026-10-23, kept on while the research caps are off). A run
  whose model tokens (input + output of the replies the model really served, the larger of the
  router's ledger and the reply log, as the token card counts them) reach ``token_guard_multiple``
  (``spend.yaml`` v2, default 3) times its pre-run estimate (``run_input.expected_tokens``) plus
  what the user already let it use (``run_input.token_guard_ok``) raises ``TokenGuardReached``
  before the next paid model call. The research graph pauses on it and asks the user; continuing
  records the current tokens as ``token_guard_ok`` (the next pause is the multiple again on top).
  A run with no estimate is never paused by it.

Neither stop is a connector error: it is not turned into one failed row, it ends the run, and the
runner keeps everything as PARTIAL with the reason (``workflow.runner._stop_unexpected``).

GUARANTEE(test_a_run_stops_partial_at_the_dollar_cap_counting_model_spend)
GUARANTEE(test_the_same_query_paid_past_the_limit_stops_the_run)
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.dev_limits import research_uncapped

PAGING = frozenset({"cursor", "page", "page_token", "offset", "limit", "per_page", "max_results"})
_SPACE = re.compile(r"\s+")


class TokenGuardReached(Exception):
    """The run's model tokens reached the guard's multiple of its estimate: pause and ask."""

    def __init__(self, tokens: int, threshold: int) -> None:
        super().__init__(f"run tokens {tokens} reached the token guard at {threshold}")
        self.tokens = tokens
        self.threshold = threshold


class DollarCapReached(Exception):
    """The run reached the platform's per-run dollar cap; it stops PARTIAL."""

    def __init__(self, cap: Decimal, spent: Decimal) -> None:
        super().__init__(f"run dollar cap {cap} reached (spent {spent})")
        self.cap = cap
        self.spent = spent


class RepeatedCallStopped(Exception):
    """The run paid for the same normalised query too often; it stops PARTIAL."""

    def __init__(self, provider: str, operation: str, times: int) -> None:
        super().__init__(f"{provider}:{operation} paid {times} times for one query")
        self.provider = provider
        self.operation = operation
        self.times = times


@dataclass(frozen=True)
class Limits:
    """The platform's run limits."""

    dollar_cap: Decimal
    repeat_call_limit: int
    stall_after_s: int


DEFAULTS = Limits(Decimal("25.00"), 3, 1800)


async def limits(s: Any) -> Limits:
    """The platform's run limits (the defaults when the row is missing)."""
    row = (
        await s.execute(
            text(
                "SELECT run_dollar_cap_usd, repeat_call_limit, stall_after_s"
                " FROM platform_defaults WHERE id = 1"
            )
        )
    ).first()
    if row is None:
        return DEFAULTS
    return Limits(
        Decimal(row.run_dollar_cap_usd), int(row.repeat_call_limit), int(row.stall_after_s)
    )


async def run_spend(s: Any, run_id: UUID) -> Decimal:
    """What the run really spent: its ledgered provider calls and its priced model replies."""
    row = (
        await s.execute(
            text(
                "SELECT coalesce((SELECT spent_usd FROM account_runs WHERE id = :r), 0)"
                " + coalesce((SELECT sum(cost_usd) FROM model_reply_log WHERE run_id = :r"
                " AND served_from = 'model'), 0) AS spent"
            ),
            {"r": run_id},
        )
    ).one()
    return Decimal(row.spent or 0)


async def check_cap(s: Any, run_id: UUID, estimate_usd: float | Decimal = 0) -> None:
    """``DollarCapReached`` when this call would take the run past the cap; moves the heartbeat.
    With the research caps off (dev_limits) only the heartbeat moves."""
    if research_uncapped():
        await touch(s, run_id)
        return
    cap = (await limits(s)).dollar_cap
    spent = await run_spend(s, run_id)
    if spent + Decimal(str(estimate_usd)) > cap or spent >= cap:
        raise DollarCapReached(cap, spent)
    await touch(s, run_id)


RUN_TOKENS = text(
    "SELECT GREATEST("
    " (SELECT COALESCE(sum(input_tokens + output_tokens), 0) FROM llm_calls WHERE run_id = :r),"
    " (SELECT COALESCE(sum(input_tokens + output_tokens), 0) FROM model_reply_log"
    "  WHERE run_id = :r AND served_from = 'model')) AS tokens"
)
TOKEN_GUARD = text(
    "SELECT run_input->>'expected_tokens' AS expected, run_input->>'token_guard_ok' AS ok"
    " FROM account_runs WHERE id = :r"
)


def token_guard_multiple() -> int:
    """How many times its estimate a run's tokens may reach before it pauses (``spend.yaml``)."""
    from spend.config import load_v2_config

    return load_v2_config().token_guard_multiple


async def run_tokens(s: Any, run_id: UUID) -> int:
    """The run's model tokens so far, counted as its token card counts them."""
    return int(await s.scalar(RUN_TOKENS, {"r": run_id}) or 0)


async def check_tokens(s: Any, run_id: UUID) -> None:
    """``TokenGuardReached`` when the run's tokens reached its guard (see the module docstring).
    Applies with the research caps off too; a run without an estimate is never stopped by it."""
    row = (await s.execute(TOKEN_GUARD, {"r": run_id})).first()
    if row is None or not row.expected or int(row.expected) <= 0:
        return
    threshold = int(row.ok or 0) + token_guard_multiple() * int(row.expected)
    used = await run_tokens(s, run_id)
    if used >= threshold:
        raise TokenGuardReached(used, threshold)


async def accept_tokens(s: Any, run_id: UUID) -> int:
    """The user continued the run past its token guard: the next pause is the guard's multiple of
    the estimate on top of the tokens used now. Returns those tokens."""
    from account_intelligence.connectors import budget

    used = await run_tokens(s, run_id)
    await budget.accept_tokens(s, run_id, used)
    return used


def query_key(provider: str, operation: str, args: dict[str, Any]) -> str:
    """The normalised query of a call (see the module docstring)."""

    def norm(value: Any) -> Any:
        if isinstance(value, str):
            return _SPACE.sub(" ", value).strip().lower()
        if isinstance(value, dict):
            return {k: norm(v) for k, v in sorted(value.items()) if k not in PAGING}
        if isinstance(value, list | tuple):
            return [norm(v) for v in value]
        return value

    body = json.dumps([provider, operation, norm(args)], sort_keys=True, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


async def check_repeat(
    s: Any, run_id: UUID, key: str, args_hash: str, provider: str, operation: str
) -> None:
    """``RepeatedCallStopped`` when another paid call for this query would pass the limit (never
    with the research caps off, dev_limits)."""
    if research_uncapped():
        return
    limit = (await limits(s)).repeat_call_limit
    paid = await s.scalar(
        text(
            "SELECT count(DISTINCT args_hash) FROM tool_call_events WHERE run_id = :r"
            " AND query_key = :q AND event = 'started' AND args_hash <> :h"
        ),
        {"r": run_id, "q": key, "h": args_hash},
    )
    if int(paid or 0) >= limit:
        raise RepeatedCallStopped(provider, operation, int(paid or 0) + 1)


async def touch(s: Any, run_id: UUID) -> None:
    """The run made progress (the heartbeat the stall sweeper reads)."""
    from account_intelligence.connectors import budget

    await budget.touch(s, run_id)
