"""Significance score (DS2, D-73): a pure, explainable weighted sum over typed fields from a graded
fact. The components and the config version are stored with the event, so a score can always be
re-derived; the same inputs always give the same score; no model is involved.

GUARANTEE(test_scores_are_pure_monotone_and_explained)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR


@dataclass(frozen=True)
class Inputs:
    """Typed inputs (from the graded fact and the account)."""

    signal_type: str
    grade: str
    source_tier: str
    observed_on: date
    lane_match: bool
    priority_a: bool


@dataclass(frozen=True)
class Score:
    """The total, its components and the config version."""

    total: int
    components: tuple[tuple[str, int], ...]
    config_version: str


@lru_cache
def load() -> dict[str, Any]:
    """``config/scoring.yaml``."""
    data: dict[str, Any] = yaml.safe_load((CONFIG_DIR / "scoring.yaml").read_text())
    return data


def score(x: Inputs, today: date, cfg: Mapping[str, Any] | None = None) -> Score:
    """The score of one event (``today`` is passed in: no clock here)."""
    c = cfg or load()
    age = (today - x.observed_on).days
    recency = 0
    for within, pts in c["recency_days"]:
        if 0 <= age <= int(within):
            recency = int(pts)
            break
    parts = (
        ("signal_type", int(c["signal_type"].get(x.signal_type, c["signal_type"]["other"]))),
        ("grade", int(c["grade"].get(x.grade, 0))),
        ("source_tier", int(c["source_tier"].get(x.source_tier, 0))),
        ("recency", recency),
        ("lane_match", int(c["lane_match"]) if x.lane_match else 0),
        ("priority_a", int(c["priority_a"]) if x.priority_a else 0),
    )
    return Score(sum(p for _, p in parts), parts, str(c["version"]))
