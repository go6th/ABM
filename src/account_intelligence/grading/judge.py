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


class JudgeVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    supported: bool
    reason: str = Field(min_length=1, max_length=300)


Outcome = Literal["supported", "unsupported", "unavailable", "not_required"]


@dataclass(frozen=True)
class JudgeResult:
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
    return engine_grade in cfg.judge.required_for_engine_grades or product_grade in cfg.judge.required_for_product_grades


async def call_judge(port: SupportJudgePort | None, claim: str, sentence: str, cfg: GradingConfig) -> JudgeResult:
    """Ask the judge; one bounded re-ask on invalid output, then unavailable. Never raises."""
    if port is None:
        return JudgeResult("unavailable", 0, "not_configured")
    attempts = 0
    for _ in range(cfg.judge.max_reasks + 1):
        attempts += 1
        try:
            raw = await port.judge(claim, sentence)
        except (NotConfigured, JudgeUnavailable, TimeoutError, ConnectionError, OSError):
            return JudgeResult("unavailable", attempts, "judge_unavailable")
        try:
            verdict = JudgeVerdict.model_validate(raw)
        except ValidationError:
            continue
        return JudgeResult("supported" if verdict.supported else "unsupported", attempts, verdict.reason)
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
