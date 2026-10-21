"""What a research is expected to use, shown before the user confirms it (owner decision
2026-10-21, while the research caps are off: "this much will be used, should I proceed").

* Tokens: model input + output tokens, counted as the run's token meter counts them (the larger
  of the router's ledger ``llm_calls`` and the research graph's ``model_reply_log``, replies the
  model really served).
* Cost: the priced model replies (the larger of the two ledgers) plus the run's paid provider
  calls (``account_runs.spent_usd``).
* Basis: the median of the workspace's last ``history_runs`` finished runs of the same kind and
  scope that used any tokens (a run the credit cap stopped is left out: it shows what the cap
  allowed, not what the research needs). With none, the rough defaults of
  ``config/usage_estimate.yaml`` apply and ``runs`` is 0.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from typing import Any

import yaml
from sqlalchemy import text

from account_intelligence.config import CONFIG_DIR

CONFIG = CONFIG_DIR / "usage_estimate.yaml"
# estimate key -> (research_jobs kinds, research_jobs scope)
KINDS: dict[str, tuple[tuple[str, ...], str]] = {
    "jobs": (("web_refresh", "scheduled_refresh"), "jobs"),
    "people": (("web_refresh", "scheduled_refresh"), "people"),
    "full": (("web_refresh", "scheduled_refresh"), "full"),
    "topic": (("web_refresh",), "topic"),
    "new_research": (("new_research",), "full"),
    "file": (("file_analysis",), "file"),
    "file_public": (("file_analysis",), "full"),
}


@dataclass(frozen=True)
class Usage:
    """Expected tokens and dollars of one research, and how many past runs it is based on."""

    tokens: int
    cost_usd: Decimal
    runs: int

    def as_json(self) -> dict[str, Any]:
        """The API shape (cost rounded to cents)."""
        return {
            "tokens": self.tokens,
            "cost_usd": float(self.cost_usd.quantize(Decimal("0.01"))),
            "runs": self.runs,
        }


@dataclass(frozen=True)
class Settings:
    """``config/usage_estimate.yaml``."""

    history_runs: int
    defaults: dict[str, Usage]


@lru_cache(maxsize=1)
def settings() -> Settings:
    """The config, validated once (an invalid file fails at startup)."""
    raw = yaml.safe_load(CONFIG.read_text())
    defaults = {}
    for key in KINDS:
        entry = raw["defaults"].get(key)
        if entry is None:
            raise ValueError(f"usage_estimate.yaml needs a default for {key}")
        tokens, cost = int(entry["tokens"]), Decimal(str(entry["cost_usd"]))
        if tokens < 1 or cost <= 0:
            raise ValueError(f"usage_estimate.yaml: the default for {key} must be positive")
        defaults[key] = Usage(tokens, cost, 0)
    return Settings(max(1, int(raw.get("history_runs", 5))), defaults)


PAST_RUNS = text(
    "SELECT"
    " GREATEST("
    "  (SELECT COALESCE(sum(input_tokens + output_tokens), 0) FROM llm_calls"
    "   WHERE run_id = r.id),"
    "  (SELECT COALESCE(sum(input_tokens + output_tokens), 0) FROM model_reply_log"
    "   WHERE run_id = r.id AND served_from = 'model')) AS tokens,"
    " GREATEST("
    "  (SELECT COALESCE(sum(cost_usd), 0) FROM llm_calls WHERE run_id = r.id),"
    "  (SELECT COALESCE(sum(cost_usd), 0) FROM model_reply_log"
    "   WHERE run_id = r.id AND served_from = 'model'))"
    " + COALESCE(r.spent_usd, 0) AS cost"
    " FROM research_jobs j JOIN account_runs r ON r.id = j.run_id"
    " WHERE j.kind = ANY(:kinds) AND j.scope = :scope AND j.status = 'done'"
    " AND j.stop_reason IS DISTINCT FROM 'credit_cap_reached'"
    " ORDER BY j.finished_at DESC NULLS LAST LIMIT :n"
)


async def estimate_usage(s: Any, key: str) -> Usage:
    """The expected use of one research of ``key`` (see the module docstring), in the caller's
    tenant transaction."""
    cfg = settings()
    kinds, scope = KINDS[key]
    rows = (
        await s.execute(
            PAST_RUNS, {"kinds": list(kinds), "scope": scope, "n": cfg.history_runs * 2}
        )
    ).all()
    used = [(int(r.tokens), Decimal(str(r.cost))) for r in rows if int(r.tokens or 0) > 0]
    used = used[: cfg.history_runs]
    if not used:
        return cfg.defaults[key]
    tokens = int(statistics.median(t for t, _ in used))
    cost = Decimal(str(statistics.median(c for _, c in used)))
    return Usage(tokens, cost, len(used))
