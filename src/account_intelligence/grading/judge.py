"""G12 judge pass. The judge sees only the claim and the cited sentence, must run on a
different model profile than the generator, and its output is schema-validated with at
most ``max_reasks`` re-asks. Unavailable or not configured means the claim is not granted
A, B or Confirmed: it is capped (engine C, Probable) and flagged ``unjudged``.
``apply_support_verdict`` is pure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from account_intelligence.config import CONFIG_DIR
from account_intelligence.grading.config import EngineGrade, GradingConfig, ProductGrade
from account_intelligence.grading.errors import ConfigError, JudgeUnavailable, NotConfigured
from account_intelligence.grading.ports import SupportJudgePort
from account_intelligence.platform.ports import NotConfigured as PlatformNotConfigured


class JudgeVerdict(BaseModel):
    """The only shape a judge answer may take (supported, reason)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    supported: bool
    reason: str = Field(min_length=1, max_length=300)


class JudgeManyVerdict(BaseModel):
    """A grouped judge answer: one verdict per sentence, in the order the sentences were sent."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    verdicts: list[JudgeVerdict] = Field(min_length=1)


Outcome = Literal["supported", "unsupported", "unavailable", "not_required"]


@dataclass(frozen=True)
class JudgeResult:
    """The judge outcome after at most one re-ask."""

    outcome: Outcome
    attempts: int
    reason: str | None = None


@dataclass(frozen=True)
class Judged:
    """Result of applying a verdict: either kept (possibly capped) or dropped."""

    keep: bool
    engine_grade: EngineGrade
    product_grade: ProductGrade
    flags: tuple[str, ...]
    drop_reason: str | None = None


def model_profile(task: str, models_path: Path | None = None) -> str:
    """The model profile a task routes to in config/models.yaml."""
    raw = yaml.safe_load((models_path or CONFIG_DIR / "models.yaml").read_text(encoding="utf-8"))
    arm = raw["arms"][raw["active_arm"]]
    return str(raw["profiles"][arm["routes"][task]]["model"])


def ensure_judge_differs(cfg: GradingConfig, judge_profile: str, generator_profile: str) -> None:
    """The judge must not be the generator's model (skill reference 06, verify node)."""
    if judge_profile == generator_profile:
        raise ConfigError(
            f"judge task {cfg.judge.judge_task!r} and generator task {cfg.judge.generator_task!r} "
            "use the same model profile"
        )


def needs_judge(engine_grade: EngineGrade, product_grade: ProductGrade, cfg: GradingConfig) -> bool:
    """Whether a claim must be judged (engine A or B, or Confirmed)."""
    return (
        engine_grade in cfg.judge.required_for_engine_grades
        or product_grade in cfg.judge.required_for_product_grades
    )


async def call_judge(
    port: SupportJudgePort | None, claim: str, sentence: str, cfg: GradingConfig
) -> JudgeResult:
    """Ask the judge; one bounded re-ask on invalid output, then unavailable. Never raises."""
    if port is None:
        return JudgeResult("unavailable", 0, "not_configured")
    attempts = 0
    # LOOP(judge re-ask): cap=max_reasks (1); exit=supported, unsupported, unavailable (claim
    # capped, G12)
    for _ in range(cfg.judge.max_reasks + 1):
        attempts += 1
        try:
            raw = await port.judge(claim, sentence)
        except (
            NotConfigured,
            PlatformNotConfigured,
            JudgeUnavailable,
            TimeoutError,
            ConnectionError,
            OSError,
        ):
            return JudgeResult("unavailable", attempts, "judge_unavailable")
        try:
            verdict = JudgeVerdict.model_validate(raw)
        except ValidationError:
            continue
        return JudgeResult(
            "supported" if verdict.supported else "unsupported", attempts, verdict.reason
        )
    return JudgeResult("unavailable", attempts, "invalid_judge_output")


async def call_judge_many(
    port: SupportJudgePort | None, claim: str, sentences: list[str], cfg: GradingConfig
) -> JudgeResult:
    """Ask the judge about every evidence sentence of one claim in ONE call (token review item
    10, 2026-10-20: was one call per sentence). Supported only when every sentence is; the
    first unsupported sentence's reason is kept. A port without ``judge_many`` (or a single
    sentence) is asked sentence by sentence as before. Never raises."""
    many = getattr(port, "judge_many", None)
    if port is None or many is None or len(sentences) < 2:
        result = JudgeResult("unavailable", 0, "not_configured")
        for sentence in sentences:
            result = await call_judge(port, claim, sentence, cfg)
            if result.outcome != "supported":
                break
        return result
    attempts = 0
    # LOOP(grouped judge re-ask): cap=max_reasks (1); exit=verdicts for every sentence,
    # unavailable (claim capped, G12)
    for _ in range(cfg.judge.max_reasks + 1):
        attempts += 1
        try:
            raw = await many(claim, sentences)
        except (
            NotConfigured,
            PlatformNotConfigured,
            JudgeUnavailable,
            TimeoutError,
            ConnectionError,
            OSError,
        ):
            return JudgeResult("unavailable", attempts, "judge_unavailable")
        try:
            grouped = JudgeManyVerdict.model_validate(raw)
        except ValidationError:
            continue
        if len(grouped.verdicts) != len(sentences):
            continue
        refused = next((v for v in grouped.verdicts if not v.supported), None)
        if refused is not None:
            return JudgeResult("unsupported", attempts, refused.reason)
        return JudgeResult("supported", attempts, grouped.verdicts[0].reason)
    return JudgeResult("unavailable", attempts, "invalid_judge_output")


def apply_support_verdict(
    engine_grade: EngineGrade,
    product_grade: ProductGrade,
    flags: tuple[str, ...],
    result: JudgeResult,
    cfg: GradingConfig,
) -> Judged:
    """Pure. Unsupported drops the claim; unavailable caps it (G12); never skips silently."""
    if not needs_judge(engine_grade, product_grade, cfg):
        return Judged(True, engine_grade, product_grade, flags)
    if result.outcome == "supported":
        return Judged(True, engine_grade, product_grade, flags)
    if result.outcome == "unsupported":
        return Judged(False, engine_grade, product_grade, flags, "unsupported_by_judge")
    cap = cfg.judge.unavailable_cap
    capped_engine: EngineGrade = cap.engine_grade if engine_grade in ("A", "B") else engine_grade
    capped_product: ProductGrade = cap.product_grade or "probable"
    if product_grade not in ("confirmed", "probable"):
        capped_product = product_grade
    return Judged(True, capped_engine, capped_product, tuple(sorted({*flags, cap.flag})))
