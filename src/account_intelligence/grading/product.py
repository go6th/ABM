"""Product grade by one pure function with configured precedence (D-01 proposal).

Order (``config/grading_policy.yaml product_precedence.order``):
1 no evidence -> Hypothesis; 2 Conflicting -> at most Probable; 3 internal-only -> Internal;
4 read in a primary source this run (computed by ``primary.py``) -> Confirmed;
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
    status: AttributeStatus
    engine_grade: EngineGrade | None
    read_in_primary: bool
    synthesis: bool = False
    internal_only: bool = False
    internal_authoritative: bool = False
    secondary_only: bool = False


def _rule(rule: str, x: ProductInputs, cfg: GradingConfig) -> ProductGrade | None:
    p = cfg.precedence
    if rule == "no_evidence":
        return "hypothesis" if x.status == "no_evidence" else None
    if rule == "conflicting":
        return p.conflicting_max_product_grade if x.status == "conflicting" else None
    if rule == "internal_only":
        # Never Confirmed from internal material alone unless the client marked it authoritative.
        return "internal" if x.internal_only and not x.internal_authoritative else None
    if rule == "primary_read":
        return "confirmed" if x.read_in_primary else None
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


def display_label(grade: ProductGrade, flags: tuple[str, ...] = (), lead_status: str | None = None) -> str:
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
