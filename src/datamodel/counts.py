"""Counts name their base; a date range alone is a Lead (DM6, roadmap 11.1). Pure.

* ``render_count``: a number is rendered only with its base ("read" or "matched", plus what was
  counted); a missing base raises ``CountWithoutBase``.
* ``tech_grade_cap``: a technology detection whose only evidence is a first/last-seen date range
  is capped at Lead until a cross-check result exists; a contradicting cross-check is a Lead too.

GUARANTEE(test_a_count_without_its_base_cannot_render)
GUARANTEE(test_a_date_range_alone_is_a_lead)
"""

from __future__ import annotations

from typing import Literal

Base = Literal["read", "matched"]
GRADES = ("lead", "hypothesis", "probable", "confirmed")


class CountWithoutBase(ValueError):
    """A number without the base it was counted over."""


def render_count(value: int, base: Base | None, what: str) -> str:
    """``"12 matched (job postings)"``; CountWithoutBase without a base."""
    if base not in ("read", "matched"):
        raise CountWithoutBase(f"{what}: a count must state whether it is read or matched")
    if value < 0 or not what.strip():
        raise CountWithoutBase("a count needs a non-negative value and what was counted")
    return f"{value} {base} ({what})"


def tech_grade_cap(proposed: str, *, has_date_range_only: bool, cross_check: str | None) -> str:
    """The grade a technology detection may carry."""
    if proposed not in GRADES:
        raise ValueError(f"unknown grade {proposed!r}")
    if cross_check == "contradicted" or (has_date_range_only and cross_check != "confirmed"):
        return "lead"
    return proposed
