"""Public grading API (the path the skill and Workflow v1 name). A facade over
``account_intelligence.grading``: one implementation per rule lives there.

The legacy functions below (``Evidence``, ``load_config``, ``recency``, ``group_strengths``,
``confidence``, ``grade``, ``grade_attribute``) keep their earlier signatures but delegate to
the new engine. ``now`` is required (fix F5); unknown tiers and classes raise typed errors (F2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from account_intelligence.grading.config import GradingConfig, load_grading_config
from account_intelligence.grading.engine import (
    AttributeGrade,
    EngineEvidence,
    GroupStrength,
    attribute_class,
    check_tier,
    engine_grade,
    quantise,
)
from account_intelligence.grading.engine import confidence as _confidence
from account_intelligence.grading.engine import grade_attribute as engine_grade_attribute
from account_intelligence.grading.engine import group_strengths as _group_strengths
from account_intelligence.grading.engine import recency as recency
from account_intelligence.grading.judge import (
    apply_support_verdict,
    call_judge,
    ensure_judge_differs,
)
from account_intelligence.grading.leads import evaluate_lead, new_lead, submit_query
from account_intelligence.grading.pipeline import (
    BatchResult,
    Dropped,
    GradingInputs,
    grade_claims,
    load_inputs,
)
from account_intelligence.grading.primary import read_in_primary_this_run
from account_intelligence.grading.product import display_label, product_grade, statement_policy
from account_intelligence.grading.repo import (
    record_drops,
    record_graded_fact,
    record_lead,
    supersede,
)
from account_intelligence.grading.schema import (
    GradedClaim,
    ProposedAbsence,
    ProposedSourced,
    ProposedSynthesis,
)


@dataclass(frozen=True)
class Evidence:
    """Legacy evidence item: dated by retrieval ('observed'), lineage known."""

    tier: str
    lineage_group: str
    retrieved_at: datetime
    value: str


def load_config(path: Path | None = None) -> tuple[dict[str, float], dict[str, float]]:
    """Legacy: (weights, half-lives) from the grading config (directory of ``path``)."""
    cfg = load_grading_config(path.parent if path is not None else None)
    return {str(k): v for k, v in cfg.weights.items()}, dict(cfg.half_lives)


def _cfg(weights: dict[str, float] | None, half_lives: dict[str, float] | None) -> GradingConfig:
    cfg = load_grading_config()
    update: dict[str, Any] = {}
    if weights is not None:
        update["weights"] = weights
    if half_lives is not None:
        update["half_lives"] = half_lives
    return cfg.model_copy(update=update) if update else cfg


def _engine(items: list[Evidence]) -> list[EngineEvidence]:
    return [
        EngineEvidence(e.tier, e.lineage_group, e.retrieved_at, "observed", e.value) for e in items
    ]


def group_strengths(
    evidence: list[Evidence],
    attr_class: str,
    now: datetime,
    weights: dict[str, float] | None = None,
    half_lives: dict[str, float] | None = None,
) -> dict[str, tuple[float, str]]:
    groups = _group_strengths(_engine(evidence), attr_class, now, _cfg(weights, half_lives))
    return {g.group: (g.strength, g.tier) for g in groups}


def _groups(strengths: dict[str, tuple[float, str]]) -> list[GroupStrength]:
    return [
        GroupStrength(k, s, check_tier(t), True, True) for k, (s, t) in sorted(strengths.items())
    ]


def confidence(strengths: dict[str, tuple[float, str]]) -> float:
    return _confidence(_groups(strengths))


def grade(value: float, strengths: dict[str, tuple[float, str]]) -> str:
    """Legacy grade for a given confidence (thresholds from config, Decimal comparison)."""
    cfg = load_grading_config()
    groups = _groups(strengths)
    c = quantise(value, cfg)
    t = cfg.thresholds
    if (
        c >= t.a
        and len(groups) >= t.a_min_groups
        and any(g.tier in t.a_required_tiers for g in groups)
    ):
        return "A"
    return "B" if c >= t.b else "C" if c >= t.c else "D"


def grade_attribute(
    evidence: list[Evidence],
    attr_class: str,
    now: datetime,
    weights: dict[str, float] | None = None,
    half_lives: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Legacy dict result (confidence rounded to 4 places) from the new engine."""
    result = engine_grade_attribute(
        _engine(evidence), attr_class, now, _cfg(weights, half_lives), normaliser="casefold_text"
    )
    if result.status == "no_evidence":
        return {"status": "no_evidence"}
    if result.status == "conflicting":
        return {
            "status": "conflicting",
            "values": {
                v.value: {
                    "confidence": round(float(v.confidence), 4),
                    "grade": v.engine_grade,
                    "groups": list(v.groups),
                }
                for v in result.values
            },
        }
    best = result.best
    assert best is not None
    return {
        "status": "graded",
        "value": best.value,
        "confidence": round(float(best.confidence), 4),
        "grade": best.engine_grade,
        "groups": list(best.groups),
    }


__all__ = [
    "AttributeGrade",
    "BatchResult",
    "Dropped",
    "EngineEvidence",
    "Evidence",
    "GradedClaim",
    "GradingConfig",
    "GradingInputs",
    "ProposedAbsence",
    "ProposedSourced",
    "ProposedSynthesis",
    "apply_support_verdict",
    "attribute_class",
    "call_judge",
    "confidence",
    "display_label",
    "engine_grade",
    "engine_grade_attribute",
    "ensure_judge_differs",
    "evaluate_lead",
    "grade",
    "grade_attribute",
    "grade_claims",
    "group_strengths",
    "load_config",
    "load_grading_config",
    "load_inputs",
    "new_lead",
    "product_grade",
    "read_in_primary_this_run",
    "record_drops",
    "record_graded_fact",
    "record_lead",
    "recency",
    "statement_policy",
    "submit_query",
    "supersede",
]
