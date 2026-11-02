"""Answer expanders (Ask redesign 3b, 2026-11-02): matches a question did not spell out but the
answer should carry, found by code so Sonnet, Haiku and Fable return the same set.

* **Aliases.** A text filter on "SFDC" also matches "Salesforce", "vp" also "vice president"
  (``config/expanders.yaml`` ``aliases``): the typed text still matches as a substring, as before,
  and the group's other spellings match as whole words. The result lists what was added.
* **Nearby.** A filter on a place column ("location contains San Francisco") also returns the
  rows whose place lies within a radius of it (50 miles unless the call asks for another),
  nearest first, each with its distance and the place it was read as (``documents.places``); the
  exact matches stay a separate set. Rows whose place is blank or not a city are counted, never
  dropped silently.

This module is pure (no store access): ``documents.tables`` runs the queries and calls it.

GUARANTEE(test_aliases_and_nearby_expand_a_filter_without_losing_the_exact_matches)
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR
from documents import places


@lru_cache(maxsize=1)
def config() -> dict[str, Any]:
    """``config/expanders.yaml``, loaded once."""
    return dict(yaml.safe_load((CONFIG_DIR / "expanders.yaml").read_text()))


def default_radius() -> float:
    """The nearby radius a call that names none gets, in miles."""
    return float(config()["nearby_radius_miles"])


def radius(asked: Any) -> float:
    """A call's radius, bounded by the configured maximum; the default for none."""
    if asked is None:
        return default_radius()
    return max(1.0, min(float(asked), float(config()["max_radius_miles"])))


@lru_cache(maxsize=1)
def _groups() -> dict[str, list[str]]:
    """Every spelling (lower case) to the other spellings of its group."""
    out: dict[str, list[str]] = {}
    for kind in (config().get("aliases") or {}).values():
        for group in kind:
            spellings = [str(s).casefold() for s in group]
            for s in spellings:
                out[s] = [o for o in spellings if o != s]
    return out


def aliases_of(value: str) -> list[str]:
    """The other spellings of ``value`` when it is one spelling of an alias group, else []."""
    return list(_groups().get(" ".join(value.casefold().split()), ()))


def word_pattern(spellings: Sequence[str]) -> str:
    """A regular expression (PostgreSQL syntax) matching any spelling as whole words."""
    return r"\m(?:" + "|".join(re.escape(s) for s in spellings) + r")\M"


def is_place_column(name: str, header: str) -> bool:
    """Whether a column holds a place: its name or header has one of the configured words."""
    words = set(config()["location_column_words"])
    tokens = set(re.split(r"[^a-z0-9]+", f"{name} {header}".casefold()))
    return bool(tokens & words)


def nearby(
    target: str,
    rows: Sequence[Mapping[str, Any]],
    column: str,
    miles: float,
) -> dict[str, Any] | None:
    """The rows (``{"row": n, "cells": {...}}``) whose ``column`` is a place within ``miles`` of
    ``target``, nearest first, each given ``distance_miles`` and ``place``; the number of rows
    with no place; None when ``target`` is not a city. The rows are the candidates (the exact
    matches are left out by the caller)."""
    here = places.resolve(target)
    if here is None:
        return None
    found: list[dict[str, Any]] = []
    unplaced = 0
    for row in rows:
        place = places.resolve(str((row.get("cells") or {}).get(column) or ""))
        if place is None:
            unplaced += 1
            continue
        distance = places.miles(here, place)
        if distance <= miles:
            found.append({**row, "distance_miles": round(distance, 1), "place": place.label})
    found.sort(key=lambda r: (r["distance_miles"], int(r["row"])))
    return {
        "target": here.label,
        "radius_miles": miles,
        "rows": found,
        "count": len(found),
        "unplaced": unplaced,
    }
