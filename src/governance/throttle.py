"""The throttle ladder (CG2, D-79): a pure function of the used share and the accounts' scores.

Below 80% nothing is reduced. From 80% the lowest-scoring share of accounts is probe-only; from
90% cadences are lengthened; from 95% non-priority monitors pause; at 100% everything stops.
Priority A accounts are reduced last (only at the hard stop), and the budget alert itself is never
throttled. Ties break by account id, so the same inputs always give the same plan.

GUARANTEE(test_the_throttle_ladder_is_deterministic_and_spares_priority_a)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

Mode = Literal["normal", "probe_only", "lengthened", "paused", "stopped"]


@dataclass(frozen=True)
class Account:
    """What the ladder needs of one monitored account."""

    account_id: str
    score: int
    priority_a: bool


def plan(
    used_percent: float, accounts: Sequence[Account], ladder: Sequence[Mapping[str, Any]]
) -> dict[str, Mode]:
    """Each account's mode at this used share."""
    steps = {str(s["step"]): s for s in ladder}
    out: dict[str, Mode] = {a.account_id: "normal" for a in accounts}
    if used_percent >= float(steps["hard_stop"]["from_percent"]):
        return {a.account_id: "stopped" for a in accounts}
    ordinary = sorted(
        (a for a in accounts if not a.priority_a), key=lambda a: (a.score, a.account_id)
    )
    s1 = steps["probe_only_lowest_scores"]
    if used_percent >= float(s1["from_percent"]):
        n = int(len(ordinary) * float(s1["share"]))
        for a in ordinary[:n]:
            out[a.account_id] = "probe_only"
    if used_percent >= float(steps["lengthen_cadence"]["from_percent"]):
        for a in ordinary:
            out[a.account_id] = "lengthened" if out[a.account_id] == "normal" else out[a.account_id]
    if used_percent >= float(steps["pause_non_priority"]["from_percent"]):
        for a in ordinary:
            out[a.account_id] = "paused"
    return out
