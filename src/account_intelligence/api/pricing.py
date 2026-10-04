"""Resolve configured credit reservations without assuming an unapproved price.

Guarantees: absent or invalid D-04 prices fail before any reservation
(GUARANTEE(test_unconfigured_credit_price_fails_closed)).
"""

from __future__ import annotations

from typing import Literal

from account_intelligence.api.errors import APIProblem


def credits_for_depth(
    depth: Literal["standard", "deep"],
    *,
    standard_credits: int | None,
    deep_credits: int | None,
) -> int:
    """Return an explicit depth price or fail closed while D-04 is unresolved."""
    credits = standard_credits if depth == "standard" else deep_credits
    if credits is None or credits <= 0:
        raise APIProblem(
            503,
            "pricing_not_configured",
            "Service temporarily unavailable",
            "Run pricing has not been configured.",
        )
    return credits
