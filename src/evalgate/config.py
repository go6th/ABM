"""Load and validate ``config/evals.yaml`` (thresholds from ARCH, the rest proposals).

GUARANTEE(test_eval_config_is_sourced): every value has a source or a status.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


class EvalConfigInvalid(ValueError):
    """``config/evals.yaml`` is invalid or unsourced."""


@dataclass(frozen=True)
class Threshold:
    """One gate: the metric must satisfy ``op value``."""

    op: str
    value: float

    def holds(self, x: float) -> bool:
        """Whether ``x`` passes."""
        return x >= self.value if self.op == ">=" else x <= self.value


@dataclass(frozen=True)
class EvalConfig:
    """The validated eval configuration."""

    thresholds: Mapping[str, Threshold]
    primary_metrics: tuple[str, ...]
    alpha: float
    bootstrap_resamples: int
    seed: int
    repeats_k: int
    calibration_bins: int
    label_max_age_days: int
    judge_agreement_floor: float
    a_a_false_block_rate_max: float
    required_modes: Mapping[str, tuple[str, ...]]
    config_hash: str


def _v(data: Mapping[str, Any], key: str) -> Any:
    entry = data.get(key)
    if not isinstance(entry, Mapping) or "value" not in entry:
        raise EvalConfigInvalid(f"{key} must be a mapping with a value")
    if "source" not in entry and "status" not in entry:
        raise EvalConfigInvalid(f"{key} has no source or status")
    return entry["value"]


def build(data: Mapping[str, Any], raw: bytes) -> EvalConfig:
    """Validate (``EvalConfigInvalid`` on anything wrong)."""
    try:
        thresholds = {}
        for name, t in _v(data, "thresholds").items():
            if t["op"] not in (">=", "<="):
                raise EvalConfigInvalid(f"threshold {name} has an unknown operator")
            thresholds[str(name)] = Threshold(str(t["op"]), float(t["value"]))
        alpha = float(_v(data, "alpha"))
        if not 0 < alpha < 1:
            raise EvalConfigInvalid("alpha must be in (0, 1)")
        modes = {str(k): tuple(str(m) for m in v) for k, v in _v(data, "required_modes").items()}
        for ms in modes.values():
            if not set(ms) <= {"replay", "live", "nightly"}:
                raise EvalConfigInvalid("required_modes names an unknown mode")
        ints = {
            k: int(_v(data, k))
            for k in (
                "bootstrap_resamples",
                "seed",
                "repeats_k",
                "calibration_bins",
                "label_max_age_days",
            )
        }
        if (
            min(
                ints["bootstrap_resamples"],
                ints["repeats_k"],
                ints["calibration_bins"],
                ints["label_max_age_days"],
            )
            < 1
        ):
            raise EvalConfigInvalid("counts must be positive")
        return EvalConfig(
            thresholds=thresholds,
            primary_metrics=tuple(str(m) for m in _v(data, "primary_metrics")),
            alpha=alpha,
            bootstrap_resamples=ints["bootstrap_resamples"],
            seed=ints["seed"],
            repeats_k=ints["repeats_k"],
            calibration_bins=ints["calibration_bins"],
            label_max_age_days=ints["label_max_age_days"],
            judge_agreement_floor=float(_v(data, "judge_agreement_floor")),
            a_a_false_block_rate_max=float(_v(data, "a_a_false_block_rate_max")),
            required_modes=modes,
            config_hash=hashlib.sha256(raw).hexdigest(),
        )
    except EvalConfigInvalid:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise EvalConfigInvalid(f"invalid eval configuration: {exc!r}") from exc


@lru_cache(maxsize=4)
def load_eval_config(directory: Path | None = None) -> EvalConfig:
    """Load ``evals.yaml``."""
    path = (directory or Path(__file__).resolve().parents[2] / "config") / "evals.yaml"
    raw = path.read_bytes()
    data = yaml.safe_load(raw)
    if not isinstance(data, Mapping):
        raise EvalConfigInvalid("evals.yaml must contain a mapping")
    return build(data, raw)
