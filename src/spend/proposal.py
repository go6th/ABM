"""Build the cap proposal (PRD step 1), every figure in code (pure).

* Lines from ``spend.estimator``; expected and high totals are exact integer sums.
* Cap = expected total plus ``headroom_pct``, rounded UP to ``rounding_step`` (a cost is never
  rounded down); a plan with nothing priceable still gets one step (a cap must be positive) and
  says so.
* Minimum viable cap = the low bounds of the mandatory operations, rounded up.
* Previews: the rows that would be Blocked ``cap_reached`` at the minimum viable cap, at the
  skill's default cap and at the proposed cap (funding by ``spend.priority`` on the high bounds).
* Every assumption and every unknown or excluded line is named. The skill's $5.00 default is
  shown beside the proposal, never used as the proposal.

GUARANTEE(test_proposal_is_deterministic_and_hash_binds_every_field): same inputs, same hash.
GUARANTEE(test_proposal_cap_covers_expected_with_headroom_rounded_up): the cap is never short.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from spend.config import SpendConfig
from spend.estimator import balance_usable, estimate_lines, item_bounds
from spend.models import BalanceSnapshot, BlockedPreview, CapProposal, OpCost, PlanItem
from spend.money import round_up_to_step, total
from spend.priority import Candidate, fund


def candidates(
    plan: Sequence[PlanItem],
    costs: Mapping[tuple[str, str], OpCost],
    balances: Mapping[str, BalanceSnapshot],
    cfg: SpendConfig,
    now: datetime,
    stage_order: Sequence[str],
) -> tuple[list[Candidate], list[str]]:
    """Fundable candidates (high bounds) and the rows that cannot be funded at all (excluded
    provider)."""
    index = {stage: i for i, stage in enumerate(stage_order)}
    out: list[Candidate] = []
    excluded: list[str] = []
    for item in plan:
        if not balance_usable(balances.get(item.provider_code), now, cfg.max_snapshot_age_minutes):
            excluded.append(item.row_key)
            continue
        _, _, high, _ = item_bounds(item, costs.get((item.provider_code, item.operation)), cfg)
        out.append(
            Candidate(
                item.row_key, item.priority, index.get(item.stage, len(index)), item.operation, high
            )
        )
    return out, list(dict.fromkeys(excluded))


def build_proposal(
    run_id: str,
    plan: Sequence[PlanItem],
    costs: Mapping[tuple[str, str], OpCost],
    balances: Mapping[str, BalanceSnapshot],
    cfg: SpendConfig,
    now: datetime,
    stage_order: Sequence[str],
) -> CapProposal:
    """The proposal (see the module docstring)."""
    lines = estimate_lines(plan, costs, balances, cfg, now)
    expected = total(line.expected_micros for line in lines)
    high = total(line.high_micros for line in lines)
    assumptions: list[str] = [
        "Prices from config/providers.yaml with their sources; credit to dollar rates are not"
        " known (V7), so credit-priced calls are held at the configured reserve.",
        f"Headroom {cfg.headroom_pct}% over the expected total, rounded up to the step.",
        "The breakdown is advisory; only the total is a hard limit (D-29).",
        "Model (LLM) spend is reported but not counted against this cap (D-29).",
    ]
    with_headroom = -(-expected * (100 + cfg.headroom_pct) // 100)
    cap = round_up_to_step(with_headroom, cfg.rounding_step_micros)
    if cap == 0:
        cap = cfg.rounding_step_micros
        assumptions.append("No operation could be priced; the cap is one rounding step.")
    mandatory_low = 0
    for item in plan:
        if item.mandatory and balance_usable(
            balances.get(item.provider_code), now, cfg.max_snapshot_age_minutes
        ):
            low, _, _, _ = item_bounds(item, costs.get((item.provider_code, item.operation)), cfg)
            mandatory_low += low
    minimum = round_up_to_step(mandatory_low, cfg.rounding_step_micros)
    cands, excluded_rows = candidates(plan, costs, balances, cfg, now, stage_order)
    previews = []
    for level in sorted({minimum, min(cfg.default_cap_micros, cap), cap}):
        blocked = fund(cands, level).unfunded_rows
        previews.append(
            BlockedPreview(cap_micros=level, rows=tuple(dict.fromkeys((*excluded_rows, *blocked))))
        )
    excluded = tuple(
        sorted({line.provider_code for line in lines if line.status == "excluded_balance"})
    )
    if excluded:
        assumptions.append(
            "Providers without a usable balance snapshot are excluded and their rows stay"
            " unfunded: " + ", ".join(excluded) + "."
        )
    return CapProposal(
        run_id=run_id,
        lines=lines,
        expected_micros=expected,
        high_micros=high,
        headroom_pct=cfg.headroom_pct,
        cap_micros=cap,
        default_cap_micros=cfg.default_cap_micros,
        minimum_viable_micros=minimum,
        blocked_previews=tuple(previews),
        sample_operations=tuple(
            sorted(
                f"{line.provider_code}:{line.operation}"
                for line in lines
                if line.status == "sample"
            )
        ),
        unknown_operations=tuple(
            sorted(
                f"{line.provider_code}:{line.operation}"
                for line in lines
                if line.status == "sample"
            )
        ),
        excluded_providers=excluded,
        snapshot_taken_at=tuple(
            sorted(
                f"{code}:{snap.status}:{snap.taken_at.isoformat()}"
                for code, snap in balances.items()
            )
        ),
        assumptions=tuple(assumptions),
        config_hash=cfg.config_hash,
    )
