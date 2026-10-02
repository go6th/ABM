"""Pure evidence grading functions based on the architecture's section 6.2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR


@dataclass(frozen=True)
class Evidence:
    tier: str
    lineage_group: str
    retrieved_at: datetime
    value: str


def load_config(path: Path | None = None) -> tuple[dict[str, float], dict[str, float]]:
    """Load source weights and recency half-lives from the versioned grading asset."""
    config_path = path or CONFIG_DIR / "grading.yaml"
    raw: dict[str, Any] = yaml.safe_load(config_path.read_text())
    engine = raw["engine"]
    weights = {
        tier.split("_", 1)[0]: float(details["weight"])
        for tier, details in engine["source_tiers"].items()
    }
    half_lives = {name: float(days) for name, days in engine["half_life_days"].items()}
    return weights, half_lives


def recency(age_days: float, half_life_days: float) -> float:
    """Return exponential half-life decay, treating future evidence as fresh."""
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    return float(0.5 ** (max(age_days, 0.0) / half_life_days))


def group_strengths(
    evidence: list[Evidence],
    attr_class: str,
    now: datetime,
    weights: dict[str, float],
    half_lives: dict[str, float],
) -> dict[str, tuple[float, str]]:
    """Use only the strongest source contribution within each lineage group."""
    half_life = half_lives[attr_class]
    strengths: dict[str, tuple[float, str]] = {}
    for item in evidence:
        age = (now - item.retrieved_at).total_seconds() / 86400.0
        strength = weights[item.tier] * recency(age, half_life)
        previous = strengths.get(item.lineage_group)
        if previous is None or strength > previous[0]:
            strengths[item.lineage_group] = (strength, item.tier)
    return strengths


def confidence(strengths: dict[str, tuple[float, str]]) -> float:
    """Compute noisy-OR confidence across independent lineage groups."""
    probability_none = 1.0
    for strength, _tier in strengths.values():
        probability_none *= 1.0 - strength
    return 1.0 - probability_none


def grade(value: float, strengths: dict[str, tuple[float, str]]) -> str:
    """Map confidence to engine grade, requiring independent evidence for grade A."""
    has_primary_or_curated = any(tier in {"T1", "T2"} for _, tier in strengths.values())
    if value >= 0.85 and len(strengths) >= 2 and has_primary_or_curated:
        return "A"
    if value >= 0.70:
        return "B"
    if value >= 0.50:
        return "C"
    return "D"


def grade_attribute(
    evidence: list[Evidence],
    attr_class: str,
    now: datetime | None = None,
    weights: dict[str, float] | None = None,
    half_lives: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Grade evidence for a normalized attribute value."""
    if not evidence:
        return {"status": "no_evidence"}
    if weights is None or half_lives is None:
        configured_weights, configured_half_lives = load_config()
        weights = weights or configured_weights
        half_lives = half_lives or configured_half_lives
    current_time = now or datetime.now(UTC)
    by_value: dict[str, list[Evidence]] = {}
    for item in evidence:
        by_value.setdefault(item.value, []).append(item)

    scored: dict[str, dict[str, Any]] = {}
    for item_value, items in by_value.items():
        strengths = group_strengths(items, attr_class, current_time, weights, half_lives)
        item_confidence = confidence(strengths)
        scored[item_value] = {
            "confidence": round(item_confidence, 4),
            "grade": grade(item_confidence, strengths),
            "groups": sorted(strengths),
        }

    strong_values = [
        item_value for item_value, result in scored.items() if result["confidence"] > 0.50
    ]
    if len(strong_values) >= 2:
        groups = [set(scored[item_value]["groups"]) for item_value in strong_values]
        if any(
            left.isdisjoint(right)
            for index, left in enumerate(groups)
            for right in groups[index + 1 :]
        ):
            return {
                "status": "conflicting",
                "values": {item_value: scored[item_value] for item_value in strong_values},
            }
    best_value = max(scored, key=lambda item_value: scored[item_value]["confidence"])
    return {"status": "graded", "value": best_value, **scored[best_value]}
