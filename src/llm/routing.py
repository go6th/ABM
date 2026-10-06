"""Candidate selection (D-48, R4, R6, R8): a pure function of config, the call's failed attempts
and candidate health. Fallback is selection for the next attempt, never a retry: the node
RetryPolicy re-executes, this module only says which candidate serves attempt n.

Order of rules:

1. the ladder of the task kind's tier, in configured order;
2. data classes (R8): a candidate must declare the call's data class, and tenant or private data
   needs a verified zero-retention flag (``True``; unknown is not verified);
3. judge independence (R6): on the judge tier, a candidate of the generator's family is excluded;
4. health: a candidate whose circuit is open is excluded;
5. history: a candidate that answered ``ModelUnavailable`` is excluded; after ``ContextTooLong``
   only a candidate with a known, larger context window qualifies; a candidate with
   ``fallback_after_retryable`` retryable failures is passed over while another remains;
6. the first remaining candidate serves; it is ``degraded`` when it is not the ladder's first.

A history ending in a never-fallback class (schema, authentication, content) is a caller defect.

GUARANTEE(test_router_choice_is_a_pure_function_of_config_attempt_and_health): property.
GUARANTEE(test_fallback_happens_only_on_the_allowed_classes): R4.
GUARANTEE(test_data_class_refusal_and_judge_independence): R6, R8.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from llm.config import Candidate, RoutingConfig
from llm.errors import (
    NEVER_FALLBACK,
    DataClassRefused,
    JudgeNotIndependent,
    NoEligibleCandidate,
    UnknownTaskKind,
)
from llm.models import Attempt

SENSITIVE = frozenset({"tenant", "private"})


@dataclass(frozen=True)
class Selection:
    """The chosen candidate and why it is degraded (if it is)."""

    candidate: Candidate
    tier: str
    degraded: bool
    reason: str | None


def select(
    cfg: RoutingConfig,
    task_kind: str,
    history: Sequence[Attempt],
    open_circuits: frozenset[str],
    data_class: str,
    generator_family: str | None = None,
) -> Selection:
    """See the module docstring."""
    kind = cfg.task_kinds.get(task_kind)
    if kind is None:
        raise UnknownTaskKind(task_kind)
    if history and history[-1].error_class in NEVER_FALLBACK:
        raise ValueError(f"{history[-1].error_class} never falls back")
    tier = cfg.tiers[kind.tier]
    ladder = [cfg.candidates[n] for n in tier.ladder]
    allowed = [
        c
        for c in ladder
        if data_class in c.data_classes
        and (data_class not in SENSITIVE or c.zero_retention is True)
    ]
    if not allowed:
        raise DataClassRefused(f"no {kind.tier} candidate may receive {data_class} data")
    if kind.tier == cfg.judge_tier and generator_family is not None:
        allowed = [c for c in allowed if c.family != generator_family]
        if not allowed:
            raise JudgeNotIndependent(f"every {kind.tier} candidate is of the generator's family")
    healthy = [c for c in allowed if c.name not in open_circuits]
    if not healthy:
        raise NoEligibleCandidate(f"every {kind.tier} candidate's circuit is open")
    gone = {a.candidate for a in history if a.error_class == "ModelUnavailable"}
    pool = [c for c in healthy if c.name not in gone]
    if history and history[-1].error_class == "ContextTooLong":
        failed = cfg.candidates[history[-1].candidate].context_window
        pool = [
            c
            for c in pool
            if failed is not None and c.context_window is not None and c.context_window > failed
        ]
    if not pool:
        raise NoEligibleCandidate(f"no {kind.tier} candidate is left for this call")
    retryable = {
        c.name: sum(1 for a in history if a.candidate == c.name and a.error_class == "Overloaded")
        for c in pool
    }
    fresh = [c for c in pool if retryable[c.name] < tier.fallback_after_retryable]
    chosen = fresh[0] if fresh else pool[-1]
    degraded = chosen.name != tier.ladder[0]
    reason = None
    if degraded:
        reason = history[-1].error_class if history else "primary_ineligible"
    return Selection(chosen, kind.tier, degraded, reason)
