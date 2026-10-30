"""Product grade by one pure function with configured precedence (D-01 proposal).

Order (``config/grading_policy.yaml product_precedence.order``):
1 no evidence -> Hypothesis; 2 Conflicting -> at most Probable (a Lead stays a Lead);
3 internal-only -> Internal;
4 read in a primary source this run (computed by ``primary.py``) -> Confirmed;
4a a keyword match or date range not read this run -> Lead (``unread_match``, platform rule 4);
4b every source an aggregator (a data feed or listing, not read at the source) -> Lead
   (``aggregator_only``: the skill's rule, and the builder's check B7 ``aggregator_not_lead``);
5 engine A or B -> Probable; 6 engine C or synthesis -> Hypothesis;
7 engine D or secondary/aggregator-only -> Lead. A Blocked attribute is a question.
Only Confirmed may be stated to a prospect (PRD Appendix B; ``grading.yaml``).
"""

from __future__ import annotations

from dataclasses import dataclass

from account_intelligence.grading.config import EngineGrade, GradingConfig, ProductGrade
from account_intelligence.grading.engine import AttributeStatus


@dataclass(frozen=True)
class ProductInputs:
    """Everything product_grade needs (status, engine grade, primary read, origin)."""

    status: AttributeStatus
    engine_grade: EngineGrade | None
    read_in_primary: bool
    synthesis: bool = False
    internal_only: bool = False
    internal_authoritative: bool = False
    secondary_only: bool = False
    unread_match: bool = False
    aggregator_only: bool = False


def _rule(rule: str, x: ProductInputs, cfg: GradingConfig) -> ProductGrade | None:
    p = cfg.precedence
    if rule == "no_evidence":
        return "hypothesis" if x.status == "no_evidence" else None
    if rule == "conflicting":
        if x.status != "conflicting":
            return None
        # the cap is a ceiling, never a promotion: a keyword match or date range not read this run
        # stays a Lead even when its sources conflict (staging 2026-10-28: such a claim came
        # out Probable and guard G2 ended a three-hour research)
        if x.unread_match and not x.read_in_primary:
            return "lead"
        # the same for support from aggregators alone: conflicting feeds are still only a Lead
        # (staging 2026-10-30: 16 conflicting aggregator-only findings came out Probable and the
        # file build refused the whole research, check B7 aggregator_not_lead)
        if x.aggregator_only and not x.read_in_primary:
            return "lead"
        return p.conflicting_max_product_grade
    if rule == "internal_only":
        # Never Confirmed from internal material alone unless the client marked it authoritative.
        return "internal" if x.internal_only and not x.internal_authoritative else None
    if rule == "primary_read":
        return "confirmed" if x.read_in_primary else None
    if rule == "unread_match":
        # A keyword match or date range not read in a primary source this run is a Lead
        # (platform rule 4; skill rule "a keyword match is a lead", guide S5).
        return "lead" if x.unread_match and not x.read_in_primary else None
    if rule == "aggregator_only":
        # aggregator support alone never makes a finding Probable (FIX_LOG D13)
        return "lead" if x.aggregator_only and not x.read_in_primary else None
    if rule == "engine_a_or_b":
        return "probable" if x.engine_grade in ("A", "B") and not x.synthesis else None
    if rule == "engine_c_or_synthesis":
        return "hypothesis" if x.synthesis or x.engine_grade == "C" else None
    if rule == "engine_d_or_secondary_only":
        return "lead" if x.engine_grade == "D" or x.secondary_only else None
    raise ValueError(f"unknown precedence rule {rule!r}")


def product_grade(x: ProductInputs, cfg: GradingConfig) -> tuple[ProductGrade, str]:
    """Return (product grade, the precedence rule that decided it). Pure and total."""
    if x.status == "blocked":
        return cfg.precedence.blocked_product_grade, "blocked"
    for rule in cfg.precedence.order:
        grade = _rule(rule, x, cfg)
        if grade is not None:
            return grade, rule
    # Unreachable with a complete order; fall back to the most cautious grade.
    return "hypothesis", "fallback"


def statement_policy(grade: ProductGrade, cfg: GradingConfig) -> bool:
    """True only when the grade may be stated to a prospect (Confirmed)."""
    return cfg.may_state[grade]


_LABELS: dict[str, str] = {
    "confirmed": "Confirmed",
    "probable": "Probable",
    "hypothesis": "Hypothesis",
    "lead": "Lead",
    "internal": "Internal Only",
}


def display_label(
    grade: ProductGrade, flags: tuple[str, ...] = (), lead_status: str | None = None
) -> str:
    """Label for deliverables. A Lead whose verification is not complete is never shown as a
    plain Lead; aggregator support is labelled."""
    label = _LABELS[grade]
    if grade == "lead":
        if lead_status in ("pending", "incomplete"):
            label = f"Lead, verification {lead_status}"
        if "aggregator" in flags:
            label = f"{label} (aggregator source)"
    if "conflicting" in flags:
        label = f"{label}, conflicting sources"
    return label
