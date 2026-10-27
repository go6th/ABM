"""The platform dictionary (``config/parity/platforms.yaml``): the lane keyword patterns of the
Job Postings workbook and the Platform Ranking dictionary, loaded once and handed to the rules
as data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR

CONFIG_PATH = CONFIG_DIR / "parity" / "platforms.yaml"


@dataclass(frozen=True)
class LanePattern:
    """One lane platform of the Job Postings workbook."""

    name: str
    pattern: str
    case_sensitive: bool = False

    @property
    def flags(self) -> int:
        """The ``re`` flags the pattern is searched with."""
        return 0 if self.case_sensitive else re.IGNORECASE

    @property
    def regex(self) -> re.Pattern[str]:
        """The compiled pattern."""
        return re.compile(self.pattern, self.flags)


@dataclass(frozen=True)
class RankedPlatform:
    """One entry of the Platform Ranking dictionary."""

    name: str
    category: str
    lane: str
    pattern: str

    @property
    def regex(self) -> re.Pattern[str]:
        """The compiled pattern (case insensitive unless the pattern turns it off)."""
        return re.compile(self.pattern, re.IGNORECASE)


@dataclass(frozen=True)
class Platforms:
    """The whole dictionary."""

    lanes: tuple[LanePattern, ...]
    ranking: tuple[RankedPlatform, ...]
    out_of_scope: tuple[str, ...]

    @property
    def lane_names(self) -> tuple[str, ...]:
        """The lane platforms in dictionary order."""
        return tuple(lane.name for lane in self.lanes)


def platforms_from_data(data: dict[str, Any]) -> Platforms:
    """Build the dictionary from the parsed configuration, refusing an out-of-scope platform."""
    lanes = tuple(
        LanePattern(str(x["name"]), str(x["pattern"]), bool(x.get("case_sensitive", False)))
        for x in data.get("lanes", [])
    )
    ranking = tuple(
        RankedPlatform(
            str(x["name"]), str(x["category"]), str(x.get("lane") or ""), str(x["pattern"])
        )
        for x in data.get("ranking", [])
    )
    out = tuple(str(x) for x in data.get("out_of_scope", []))
    banned = re.compile("|".join(re.escape(x) for x in out), re.IGNORECASE) if out else None
    names = [lane.name for lane in lanes] + [entry.name for entry in ranking]
    if banned is not None:
        for name in names:
            if banned.search(name):
                raise ValueError(f"out of scope platform in the dictionary: {name}")
    return Platforms(lanes, ranking, out)


def load_platforms(path: Path | None = None) -> Platforms:
    """Read the dictionary from ``path`` (the repository file by default)."""
    with open(path or CONFIG_PATH, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return platforms_from_data(data)


@lru_cache(maxsize=1)
def default_platforms() -> Platforms:
    """The repository dictionary, read once."""
    return load_platforms()
