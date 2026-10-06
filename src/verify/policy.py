"""Verdict to action, downgrade only (V3). Pure.

``cap_claim`` applies the grading engine's own G12 cap (``judge.unavailable_cap`` of
``config/grading_policy.yaml``: one implementation of what an unverifiable claim becomes) to a
graded claim and adds the verifier's flag. It can only lower a grade: the result's engine and
product grades are never above the input's (property-tested), it never edits the value, the
evidence or the source, and Confirmed always loses its permission to be stated to a prospect.

GUARANTEE(test_policy_never_upgrades): property test over every grade and outcome.
"""

from __future__ import annotations

from collections.abc import Mapping

from account_intelligence.grading.config import Cap
from account_intelligence.grading.schema import GradedClaim

PRODUCT_RANK = {"confirmed": 4, "probable": 3, "hypothesis": 2, "lead": 1, "internal": 0}
ENGINE_RANK = {"A": 5, "B": 4, "C": 3, "D": 2, "CONFLICTING": 1, "BLOCKED": 0}


def action_for(outcome: str, actions: Mapping[str, str]) -> str:
    """The configured action of an outcome (unknown outcomes are refused)."""
    if outcome not in actions:
        raise ValueError(f"unknown verifier outcome {outcome}")
    return actions[outcome]


def lower_product(current: str, cap: str | None) -> str:
    """The lower of the current product grade and the cap (never higher than ``current``)."""
    if cap is None or PRODUCT_RANK[cap] >= PRODUCT_RANK[current]:
        return current
    return cap


def lower_engine(current: str | None, cap: str) -> str | None:
    """The lower of the current engine grade and the cap."""
    if current is None or ENGINE_RANK[cap] >= ENGINE_RANK[current]:
        return current
    return cap


def cap_claim(claim: GradedClaim, cap: Cap, flag: str) -> GradedClaim:
    """The capped claim (see the module docstring)."""
    product = lower_product(claim.product_grade, cap.product_grade or "probable")
    engine = lower_engine(claim.engine_grade, cap.engine_grade)
    data = claim.model_dump()
    data.update(
        product_grade=product,
        engine_grade=engine,
        may_state_to_prospect=product == "confirmed",
        flags=tuple(sorted({*claim.flags, cap.flag, flag})),
    )
    # G8: a lowered grade states its reason in code-written text (never model text)
    if product == "probable" and not claim.reasoning:
        data["reasoning"] = (
            f"Graded by the evidence engine; capped because the source re-read gave {flag}."
        )
    if product == "hypothesis" and not claim.confirming_question:
        data["confirming_question"] = f"Can the account confirm {claim.attribute_key}?"
    return GradedClaim.model_validate(data)
