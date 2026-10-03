"""Pure evidence engine (Architecture 6.2; skill reference 06; scripts/grading_reference.py).

No I/O, no clock, no environment, no model calls. Every number comes from ``GradingConfig``.
Fixes over the reference (docs/grading/reference-findings.md, each proven by a test):

* F1 deterministic: values are ranked by confidence, then lineage-group count, then the
  normalised value; groups are processed in sorted order; within a group ties prefer the
  better tier. Any permutation of the evidence gives identical output.
* F2 typed failures: ``UnknownTier``, ``UnknownAttributeClass``, ``NaiveDatetime``.
* F3 age is measured from the evidence date (published or observed), not retrieval; an
  evidence item with an unknown date may count toward confidence but cannot carry grade A.
* F4 confidence is quantised (``confidence_decimals``) and compared as ``Decimal`` against
  thresholds parsed from the config, so exact boundaries behave as written (>= 0.85 is A).
* F5 ``now`` is a required, timezone-aware argument.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal

from account_intelligence.grading.config import TIERS, EngineGrade, GradingConfig, Normaliser, Tier
from account_intelligence.grading.errors import UnknownAttributeClass, UnknownTier, require_aware

DateKind = Literal["observed", "published", "unknown"]
AttributeStatus = Literal["no_evidence", "graded", "conflicting", "blocked"]


@dataclass(frozen=True)
class EngineEvidence:
    """One evidence item as the engine sees it. ``value`` is already normalised (in code)."""

    tier: str
    lineage_group: str
    evidence_date: datetime
    date_kind: DateKind
    value: str
    lineage_known: bool = True


@dataclass(frozen=True)
class GroupStrength:
    """One lineage group's strongest contribution and its tier."""

    group: str
    strength: float
    tier: Tier
    date_known: bool
    lineage_known: bool


@dataclass(frozen=True)
class ValueScore:
    """A candidate value's confidence, engine grade and evidence groups."""

    value: str
    confidence: Decimal
    engine_grade: EngineGrade
    groups: tuple[str, ...]
    flags: tuple[str, ...]


@dataclass(frozen=True)
class AttributeGrade:
    """An attribute's outcome: graded, conflicting or blocked, with the best value."""

    status: AttributeStatus
    best: ValueScore | None
    values: tuple[ValueScore, ...]
    flags: tuple[str, ...] = ()
    blocked_reason: str | None = None


_TIER_RANK = {t: i for i, t in enumerate(TIERS)}


def check_tier(tier: str) -> Tier:
    """The tier, or UnknownTier (F2: never a silent default)."""
    if tier not in _TIER_RANK:
        raise UnknownTier(f"unknown source tier {tier!r}")
    return tier


def attribute_class(attribute_key: str, cfg: GradingConfig) -> str:
    """Half-life class of a dotted attribute key, by its first segment (config)."""
    prefix = attribute_key.split(".", 1)[0]
    cls = cfg.attribute_classes.get(prefix)
    if cls is None or cls not in cfg.half_lives:
        raise UnknownAttributeClass(f"no attribute class for {attribute_key!r}")
    return cls


def normaliser_for(attribute_key: str, cfg: GradingConfig) -> Normaliser:
    """The configured normaliser for an attribute's class (opaque when none)."""
    return cfg.normalisers.get(attribute_key.split(".", 1)[0], "opaque")


def recency(age_days: float, half_life_days: float) -> float:
    """``r = 0.5 ** (age / half_life)``; a future or zero age counts as fresh."""
    if half_life_days <= 0:
        raise ValueError("half_life_days must be positive")
    return float(0.5 ** (max(age_days, 0.0) / half_life_days))


def group_strengths(
    evidence: Iterable[EngineEvidence], attr_class: str, now: datetime, cfg: GradingConfig
) -> tuple[GroupStrength, ...]:
    """Strongest ``w * r`` per lineage group (max, never sum), sorted by group id."""
    require_aware(now, "now")
    if attr_class not in cfg.half_lives:
        raise UnknownAttributeClass(f"unknown attribute class {attr_class!r}")
    half_life = cfg.half_lives[attr_class]
    best: dict[str, GroupStrength] = {}
    for item in evidence:
        tier = check_tier(item.tier)
        require_aware(item.evidence_date, "evidence_date")
        age = (now - item.evidence_date).total_seconds() / 86400.0
        candidate = GroupStrength(
            group=item.lineage_group,
            strength=cfg.weights[tier] * recency(age, half_life),
            tier=tier,
            date_known=item.date_kind != "unknown",
            lineage_known=item.lineage_known,
        )
        current = best.get(item.lineage_group)
        if current is None or _stronger(candidate, current):
            best[item.lineage_group] = candidate
    return tuple(best[g] for g in sorted(best))


def _stronger(a: GroupStrength, b: GroupStrength) -> bool:
    """Order-independent choice of a group's representative contributor."""
    key_a = (a.strength, -_TIER_RANK[a.tier], a.date_known, a.lineage_known)
    key_b = (b.strength, -_TIER_RANK[b.tier], b.date_known, b.lineage_known)
    return key_a > key_b


def confidence(groups: Sequence[GroupStrength]) -> float:
    """Noisy-OR across independent groups: ``c = 1 - prod(1 - s_i)`` (sorted order)."""
    p_none = 1.0
    for g in sorted(groups, key=lambda x: x.group):
        p_none *= 1.0 - g.strength
    return 1.0 - p_none


def quantise(value: float, cfg: GradingConfig) -> Decimal:
    """Round a confidence to the configured decimals before threshold comparison (F4)."""
    step = Decimal(1).scaleb(-cfg.confidence_decimals)
    return Decimal(repr(value)).quantize(step, rounding=ROUND_HALF_EVEN)


def _a_eligible(groups: Sequence[GroupStrength], cfg: GradingConfig) -> bool:
    t = cfg.thresholds
    return (
        quantise(confidence(groups), cfg) >= t.a
        and len(groups) >= t.a_min_groups
        and any(g.tier in t.a_required_tiers for g in groups)
    )


def engine_grade(
    groups: Sequence[GroupStrength], cfg: GradingConfig
) -> tuple[EngineGrade, tuple[str, ...]]:
    """Grade from the groups, with the D-14 (unknown date) and D-11 (unknown lineage) caps."""
    t = cfg.thresholds
    c = quantise(confidence(groups), cfg)
    flags: list[str] = []
    if _a_eligible(groups, cfg):
        if not _a_eligible([g for g in groups if g.date_known], cfg):
            flags.append(cfg.date_cap.flag)
        if not _a_eligible([g for g in groups if g.lineage_known], cfg):
            flags.append(cfg.lineage_cap.flag)
        return ("B", tuple(flags)) if flags else ("A", ())
    if c >= t.b:
        return "B", ()
    if c >= t.c:
        return "C", ()
    return "D", ()


def score_value(
    value: str, items: Sequence[EngineEvidence], attr_class: str, now: datetime, cfg: GradingConfig
) -> ValueScore:
    """Confidence and engine grade of one value from its evidence (pure, order independent)."""
    groups = group_strengths(items, attr_class, now, cfg)
    grade, flags = engine_grade(groups, cfg)
    unknown = [g.group for g in groups if not g.lineage_known]
    lineage_flags = ("lineage_unknown",) if unknown else ()
    return ValueScore(
        value=value,
        confidence=quantise(confidence(groups), cfg),
        engine_grade=grade,
        groups=tuple(g.group for g in groups),
        flags=tuple(sorted(set(flags + lineage_flags))),
    )


def grade_attribute(
    evidence: Sequence[EngineEvidence],
    attr_class: str,
    now: datetime,
    cfg: GradingConfig,
    *,
    normaliser: Normaliser = "opaque",
    blocked_reason: str | None = None,
) -> AttributeGrade:
    """Grade one attribute. Never returns a grade for empty evidence (``no_evidence``)."""
    require_aware(now, "now")
    if blocked_reason is not None:
        return AttributeGrade("blocked", None, (), ("blocked",), blocked_reason)
    if not evidence:
        return AttributeGrade("no_evidence", None, ())
    by_value: dict[str, list[EngineEvidence]] = {}
    for item in evidence:
        by_value.setdefault(item.value, []).append(item)
    scores = sorted(
        (score_value(v, items, attr_class, now, cfg) for v, items in by_value.items()),
        key=lambda s: (-s.confidence, -len(s.groups), s.value),
    )
    strong = [s for s in scores if s.confidence > cfg.thresholds.conflict_above]
    independent = any(
        set(a.groups).isdisjoint(b.groups) for i, a in enumerate(strong) for b in strong[i + 1 :]
    )
    if independent:
        if normaliser == "opaque":
            # Without a configured normaliser, different strings may be the same fact.
            return AttributeGrade("graded", scores[0], tuple(scores), ("possible_conflict",))
        conflicting = tuple(
            ValueScore(s.value, s.confidence, "CONFLICTING", s.groups, s.flags) for s in strong
        )
        return AttributeGrade("conflicting", None, conflicting, ("conflicting",))
    return AttributeGrade("graded", scores[0], tuple(scores))
