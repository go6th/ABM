"""Token use of one run for the run screen's token card: the total, the tokens of each of the eight
steps, the priced cost of the model replies, the tokens reused from earlier research (free), and
what a research of the same kind used before (the median of the workspace's past runs), and
every step that fell back from a Kimi K3 or Grok 4.5 choice to Claude with what it added to the
bill (``fallbacks``, from the run's model record: why the bill differs from the estimate,
ADR-0119).

The total is what the token meter has always counted: input plus output tokens of the replies the
model really served, the larger of the router's ledger (``llm_calls``) and the research graph's
reply log (``model_reply_log``). Steps come from the reply log only (the router's ledger has no
graph steps), by task kind; an extraction call made after the run's first sense check belongs to
"Follow-up searches" (``targeted_pulls`` reads and grades what the follow-up searches returned).

GUARANTEE(test_token_use_splits_calls_into_the_eight_steps)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.replica.usage_estimate import KINDS, estimate_usage
from views.progress import ABM_STEPS

EXTRACT_KINDS = frozenset({"abm_pass1_extract", "abm_pass2_extract", "abm_people_extract"})
# the step each research-graph task kind belongs to (``config/agents.yaml``); extraction is
# "read" before the first sense check and "follow_up" after it
TASK_STEP: Mapping[str, str] = {
    "abm_setup": "setup",
    "abm_sense_check": "follow_up",
    "abm_distill": "write",
}
OTHER = ("other", "Other model calls")


@dataclass(frozen=True)
class StepTokens:
    """The tokens one step's model calls used."""

    id: str
    name: str
    tokens: int


def split_steps(rows: Iterable[tuple[str, bool, int]]) -> list[StepTokens]:
    """The eight steps in order with their tokens, from ``(task_kind, after_first_sense_check,
    tokens)`` rows; a task kind of no step is counted as "Other model calls" (listed only when it
    used any)."""
    used: dict[str, int] = {}
    for kind, late, tokens in rows:
        if kind in EXTRACT_KINDS:
            step = "follow_up" if late else "read"
        else:
            step = TASK_STEP.get(kind, OTHER[0])
        used[step] = used.get(step, 0) + max(0, int(tokens))
    steps = [StepTokens(sid, name, used.get(sid, 0)) for sid, name, _ in ABM_STEPS]
    if used.get(OTHER[0]):
        steps.append(StepTokens(OTHER[0], OTHER[1], used[OTHER[0]]))
    return steps


def estimate_key(kind: str | None, scope: str | None) -> str | None:
    """The usage-estimate key of a research job's kind and scope (None: no estimate applies)."""
    for key, (kinds, key_scope) in KINDS.items():
        if kind in kinds and scope == key_scope:
            return key
    return None


TOTALS = text(
    "SELECT"
    " (SELECT COALESCE(sum(input_tokens + output_tokens), 0) FROM llm_calls"
    "  WHERE run_id = :r) AS ledger_tokens,"
    " (SELECT COALESCE(sum(cost_usd), 0) FROM llm_calls WHERE run_id = :r) AS ledger_cost,"
    " (SELECT COALESCE(sum(input_tokens + output_tokens), 0) FROM model_reply_log"
    "  WHERE run_id = :r AND served_from = 'model') AS log_tokens,"
    " (SELECT COALESCE(sum(cost_usd), 0) FROM model_reply_log"
    "  WHERE run_id = :r AND served_from = 'model') AS log_cost,"
    " (SELECT COALESCE(sum(input_tokens + output_tokens), 0) FROM model_reply_log"
    "  WHERE run_id = :r AND served_from IN ('cache', 'run_replay')) AS reused_tokens"
)

BY_STEP = text(
    "WITH first_sense AS (SELECT min(id) AS id FROM model_reply_log"
    "  WHERE run_id = :r AND served_from = 'model' AND task_kind = 'abm_sense_check')"
    " SELECT l.task_kind, (f.id IS NOT NULL AND l.id > f.id) AS late,"
    "  sum(l.input_tokens + l.output_tokens) AS tokens"
    " FROM model_reply_log l CROSS JOIN first_sense f"
    " WHERE l.run_id = :r AND l.served_from = 'model'"
    " GROUP BY 1, 2"
)

# the research job that started the run (its kind and scope pick the estimate; its start and end
# times are the run time), the run's own times when no job started it
JOB = text(
    "SELECT j.kind, j.scope, COALESCE(j.started_at, r.created_at) AS started_at,"
    " COALESCE(j.finished_at, r.finished_at) AS finished_at, r.models"
    " FROM account_runs r LEFT JOIN LATERAL (SELECT kind, scope, started_at, finished_at"
    "  FROM research_jobs WHERE run_id = r.id ORDER BY created_at LIMIT 1) j ON true"
    " WHERE r.id = :r"
)


def _usd(value: Any) -> float | None:
    """A stored decimal amount as dollars (None: not recorded or not priced)."""
    try:
        return float(Decimal(str(value))) if value is not None else None
    except ArithmeticError:
        return None


def fallbacks(models: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """The run's model fallbacks as the token card shows them: the step, both models by their
    display names, why, what the discarded attempts cost and what the fallback added."""
    from llm.choice import catalogue

    cat = catalogue()

    def name(model_id: Any) -> str:
        choice = cat.get(str(model_id))
        return choice.name if choice is not None else str(model_id)

    return [
        {
            "step": str(f.get("step") or ""),
            "from_model": name(f.get("from")),
            "to_model": name(f.get("to")),
            "reason": str(f.get("reason") or ""),
            "discarded_usd": _usd(f.get("discarded_usd")),
            "extra_usd": _usd(f.get("extra_usd")),
        }
        for f in ((models or {}).get("fallbacks") or [])
        if isinstance(f, Mapping)
    ]


def _iso(at: datetime | None) -> str | None:
    return at.isoformat() if at is not None else None


async def token_use(s: Any, run_id: UUID) -> dict[str, Any]:
    """The run's token card (the ``TokenUse`` API shape), in the caller's tenant transaction."""
    t = (await s.execute(TOTALS, {"r": run_id})).one()
    from_log = int(t.log_tokens) >= int(t.ledger_tokens)
    total = max(int(t.ledger_tokens), int(t.log_tokens))
    cost = Decimal(str(t.log_cost if from_log else t.ledger_cost))
    steps: list[StepTokens] = []
    if from_log and total:
        rows = (await s.execute(BY_STEP, {"r": run_id})).all()
        steps = split_steps((r.task_kind, bool(r.late), int(r.tokens or 0)) for r in rows)
    job = (await s.execute(JOB, {"r": run_id})).first()
    key = estimate_key(job.kind, job.scope) if job is not None else None
    # a run of no known kind (a direct API run) shows no expectation
    expected = await estimate_usage(s, key) if key is not None else None
    return {
        "total": total,
        "reused": int(t.reused_tokens),
        "cost_usd": float(cost.quantize(Decimal("0.01"))),
        "steps": [{"id": st.id, "name": st.name, "tokens": st.tokens} for st in steps],
        "started_at": _iso(job.started_at) if job is not None else None,
        "finished_at": _iso(job.finished_at) if job is not None else None,
        "expected_tokens": expected.tokens if expected is not None else None,
        "expected_runs": expected.runs if expected is not None else 0,
        "fallbacks": fallbacks(job.models if job is not None else None),
    }
