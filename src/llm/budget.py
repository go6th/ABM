"""The per-run model token cap per depth tier (R9), checked before the call.

The estimate is an upper bound: the input's UTF-8 byte length (a token is at least one byte for
the tokenizers in use: [VERIFY] V1) plus the task kind's fixed ``max_tokens``. A cap that is not
configured (``null``, no source gives a number) cannot be enforced: the check reports ``unset``
and the call record carries it, so the gap is visible, never hidden.

GUARANTEE(test_budget_refuses_before_the_call): R9.
"""

from __future__ import annotations

from dataclasses import dataclass

from llm.errors import BudgetRefused


@dataclass(frozen=True)
class BudgetCheck:
    """The outcome of a pre-call check."""

    status: str  # "ok" or "unset"
    estimate: int
    used: int
    cap: int | None


def estimate_tokens(prefix: str, user: str, max_tokens: int) -> int:
    """Upper-bound tokens for one call."""
    return len(prefix.encode()) + len(user.encode()) + max_tokens


def check(used: int, estimate: int, cap: int | None) -> BudgetCheck:
    """``BudgetRefused`` when the cap cannot cover the call."""
    if cap is None:
        return BudgetCheck("unset", estimate, used, None)
    if used + estimate > cap:
        raise BudgetRefused(f"token cap {cap} cannot cover {estimate} more (used {used})")
    return BudgetCheck("ok", estimate, used, cap)
