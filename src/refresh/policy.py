"""Shelf-life policy (SL1, D-71): data with provenance; due dates by a pure function.

* Every signal type has a cadence (weekly, monthly, quarterly) or ``None``; ``None`` is never
  scheduled (not "default weekly").
* ``due_at = last_checked_at + shelf_life``; a row never checked is due at once (when it has a
  cadence).
* A tenant override may only lengthen a shelf life, within the configured bounds.
* Policy changes affect future due dates only: nothing here touches history.

Pure (no clock, no I/O beyond loading the YAML).

GUARANTEE(test_due_dates_follow_the_policy_and_null_is_never_due)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR

CADENCES = ("weekly", "monthly", "quarterly")


class PolicyInvalid(ValueError):
    """The shelf-life configuration breaks a rule."""


@dataclass(frozen=True)
class ShelfLife:
    """One signal type's policy entry."""

    signal: str
    cadence: str | None
    days: int | None
    provenance: str


@dataclass(frozen=True)
class Policy:
    """The whole table plus override bounds."""

    version: str
    entries: dict[str, ShelfLife]
    min_factor: float
    max_factor: float

    def entry(self, signal: str) -> ShelfLife:
        """The entry for a signal type (KeyError for an unknown one: no default)."""
        return self.entries[signal]


def parse(raw: dict[str, Any]) -> Policy:
    """Validate a policy document."""
    days_of = raw["cadence_days"]
    if set(days_of) != set(CADENCES) or any(int(v) <= 0 for v in days_of.values()):
        raise PolicyInvalid("cadence_days must give positive days for weekly, monthly, quarterly")
    bounds = raw["tenant_override_bounds"]
    lo, hi = float(bounds["min_factor"]), float(bounds["max_factor"])
    if not 1.0 <= lo <= hi:
        raise PolicyInvalid("overrides may only lengthen a shelf life (1 <= min <= max)")
    entries: dict[str, ShelfLife] = {}
    for signal, spec in raw["signals"].items():
        cadence = spec.get("cadence")
        provenance = spec.get("source") or spec.get("status")
        if not provenance:
            raise PolicyInvalid(f"{signal}: every value needs a source or a status")
        if cadence is not None and cadence not in CADENCES:
            raise PolicyInvalid(f"{signal}: unknown cadence {cadence!r}")
        days = int(days_of[cadence]) if cadence else None
        entries[str(signal)] = ShelfLife(str(signal), cadence, days, str(provenance))
    return Policy(str(raw["version"]), entries, lo, hi)


@lru_cache
def load() -> Policy:
    """``config/shelf_life.yaml``."""
    return parse(yaml.safe_load((CONFIG_DIR / "shelf_life.yaml").read_text()))


def effective_days(
    entry: ShelfLife, policy: Policy, override_days: int | None = None
) -> int | None:
    """The shelf life in days after a tenant override (None: never scheduled)."""
    if entry.days is None:
        return None
    if override_days is None:
        return entry.days
    if not entry.days * policy.min_factor <= override_days <= entry.days * policy.max_factor:
        raise PolicyInvalid(f"{entry.signal}: override {override_days} days is out of bounds")
    return override_days


def due_at(last_checked_at: datetime, days: int | None) -> datetime | None:
    """When a checked row is next due; ``None`` when it is never scheduled."""
    return None if days is None else last_checked_at + timedelta(days=days)


def is_due(now: datetime, last_checked_at: datetime | None, days: int | None) -> bool:
    """Whether a row is due at ``now``."""
    if days is None:
        return False
    if last_checked_at is None:
        return True
    return now >= last_checked_at + timedelta(days=days)
