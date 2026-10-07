"""Lead handling (SM3, D-75): a keyword or listing match is a Lead until read. A monitor only
proposes a candidate; the grading engine grades it. This module decides what a candidate may
trigger. Pure.

* An immediate-tier rule needs the item read and verified IN THIS refresh at (or above) the grade
  the rule names; a Lead alone never triggers an immediate alert.
* An unverified Lead may appear only in the digest, labelled "Lead, not yet verified".
* An unverifiable candidate (no evidence to read) is dropped with a counted reason.

GUARANTEE(test_a_lead_never_triggers_an_immediate_alert)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from refresh.delta import GRADE_ORDER

LEAD_LABEL = "Lead, not yet verified"
Route = Literal["immediate", "digest_as_lead", "digest", "drop"]


@dataclass(frozen=True)
class Candidate:
    """A monitor's proposal after grading."""

    grade: str
    read_in_this_refresh: bool
    has_evidence: bool


def meets(grade: str, floor: str) -> bool:
    """Whether ``grade`` is at or above ``floor`` in the product grade order."""
    return grade in GRADE_ORDER and GRADE_ORDER.index(grade) >= GRADE_ORDER.index(floor)


def route(c: Candidate, *, rule_tier: Literal["immediate", "digest"], grade_floor: str) -> Route:
    """Where a candidate may go for a rule."""
    if not c.has_evidence:
        return "drop"
    verified = c.read_in_this_refresh and c.grade != "lead"
    if rule_tier == "immediate":
        if verified and meets(c.grade, grade_floor):
            return "immediate"
        return "digest_as_lead" if c.grade == "lead" or not c.read_in_this_refresh else "drop"
    if c.grade == "lead" or not c.read_in_this_refresh:
        return "digest_as_lead"
    return "digest" if meets(c.grade, grade_floor) else "drop"
