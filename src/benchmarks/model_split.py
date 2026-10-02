"""Validate and summarize the paired three-account model-split benchmark."""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.db.data_layer import DataLayerError

ARMS = ("single", "split")
METRIC_KEYS = ("coverage", "citation_validity", "cost_usd", "elapsed_seconds")


@dataclass(frozen=True)
class ArmMetrics:
    coverage: float
    citation_validity: float
    cost_usd: float
    elapsed_seconds_mean: float


def _ratio(value: Any, *, label: str) -> float:
    if not isinstance(value, dict):
        raise DataLayerError(f"{label} must contain numerator and denominator")
    numerator, denominator = value.get("numerator"), value.get("denominator")
    if (
        not isinstance(numerator, int)
        or isinstance(numerator, bool)
        or not isinstance(denominator, int)
        or isinstance(denominator, bool)
        or denominator <= 0
        or numerator < 0
        or numerator > denominator
    ):
        raise DataLayerError(f"{label} requires 0 <= numerator <= denominator")
    return numerator / denominator


def summarize_arm(accounts: list[dict[str, Any]], arm: str) -> ArmMetrics:
    if arm not in ARMS or len(accounts) != 3:
        raise DataLayerError("benchmark summary requires one arm and exactly three accounts")
    coverage_values: list[float] = []
    citation_values: list[float] = []
    costs: list[float] = []
    elapsed: list[float] = []
    for account in accounts:
        metrics = account.get("arms", {}).get(arm)
        if not isinstance(metrics, dict) or set(metrics) != set(METRIC_KEYS):
            raise DataLayerError(f"account is missing complete {arm} benchmark metrics")
        coverage_values.append(_ratio(metrics["coverage"], label="coverage"))
        citation_values.append(_ratio(metrics["citation_validity"], label="citation_validity"))
        for key, values in (("cost_usd", costs), ("elapsed_seconds", elapsed)):
            value = metrics[key]
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise DataLayerError(f"{key} must be a non-negative number")
            values.append(float(value))
    return ArmMetrics(
        coverage=statistics.mean(coverage_values),
        citation_validity=statistics.mean(citation_values),
        cost_usd=statistics.mean(costs),
        elapsed_seconds_mean=statistics.mean(elapsed),
    )


def load_real_benchmark(path: Path) -> dict[str, Any]:
    """Reject synthetic or incomplete files; never turn fixtures into reported results."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataLayerError(f"cannot load model benchmark input {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("dataset_type") != "real_september_accounts":
        raise DataLayerError("model benchmark input must be labelled real_september_accounts")
    accounts = data.get("accounts")
    if not isinstance(accounts, list) or len(accounts) != 3:
        raise DataLayerError("model benchmark requires exactly three September accounts")
    ids = [item.get("account_id") for item in accounts if isinstance(item, dict)]
    if len(ids) != 3 or any(not isinstance(value, str) or not value.strip() for value in ids):
        raise DataLayerError("each benchmark account requires a non-empty account_id")
    if len(set(ids)) != 3:
        raise DataLayerError("model benchmark account IDs must be unique")
    for arm in ARMS:
        summarize_arm(accounts, arm)
    return data
