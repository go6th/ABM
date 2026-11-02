"""Places: a location text from a file ("San Francisco, CA, USA", "Greater Boston Area", "SF") to a
city with coordinates, and the distance between two (Ask "nearby" expander, 2026-11-02).

The city list is vendored, ``config/places.json`` (``scripts/build_places.py``): every US city of
15,000 people or more and every other of 100,000 or more, from GeoNames (CC BY 4.0). Nothing is
looked up online and no file text leaves the process.

A text is resolved in a fixed order, so the same text gives the same place on every run:

1. aliases (``config/expanders.yaml`` ``place_aliases``: "SF", "Bay Area", "NYC") on the whole
   text, then on its part before the first comma;
2. the first comma-part, noise words dropped (``place_noise``: "Greater", "Metro", "Area",
   "Remote"…), looked up by its accent-folded lowercase name;
3. among cities of that name, the ones the rest of the text names (a US state code or name, a
   country name or code), else the most populous.

A text with no city in it ("Remote", "United States", "") has no place: None, never a guess.

GUARANTEE(test_places_resolve_variants_and_measure_distance)
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR

EARTH_MILES = 3958.8
_SPLIT = re.compile(r"[,;|/]| [-–—] ")
_WORDS = re.compile(r"[^a-z0-9 .']+")


@dataclass(frozen=True)
class Place:
    """A city."""

    name: str
    country: str
    admin1: str
    lat: float
    lon: float
    population: int

    @property
    def label(self) -> str:
        """``San Francisco, CA, US`` (the state for a US city)."""
        return (
            f"{self.name}, {self.admin1}, US"
            if self.country == "US"
            else (f"{self.name}, {self.country}")
        )


def fold(text: str) -> str:
    """Accents folded, lower case, punctuation but ``.`` and ``'`` turned to spaces, spaces
    collapsed."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return " ".join(_WORDS.sub(" ", plain).split())


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    cfg = yaml.safe_load((CONFIG_DIR / "expanders.yaml").read_text())
    doc = json.loads((CONFIG_DIR / "places.json").read_text())
    by_name: dict[str, list[Place]] = {}
    for name, ascii_name, cc, admin1, lat, lon, pop in doc["places"]:
        place = Place(name, cc, admin1, lat, lon, pop)
        for key in {fold(name), fold(ascii_name)}:
            by_name.setdefault(key, []).append(place)
    for places in by_name.values():
        places.sort(key=lambda p: -p.population)
    return {
        "by_name": by_name,
        "countries": {fold(n): cc for cc, n in doc["countries"].items()}
        | {cc.casefold(): cc for cc in doc["countries"]}
        | {"usa": "US", "united states of america": "US", "uk": "GB", "england": "GB"},
        "states": {fold(n): code for code, n in doc["us_states"].items()}
        | {code.casefold(): code for code in doc["us_states"]},
        "aliases": {fold(k): str(v) for k, v in (cfg.get("place_aliases") or {}).items()},
        "noise": frozenset(fold(w) for w in (cfg.get("place_noise") or ())),
    }


def _lookup(name: str, hints: list[str]) -> Place | None:
    data = _data()
    candidates: list[Place] | None = data["by_name"].get(name)
    if not candidates:
        return None
    state: str | None = next((data["states"][h] for h in hints if h in data["states"]), None)
    country: str | None = next(
        (data["countries"][h] for h in hints if h in data["countries"]), None
    )
    if state is not None:
        in_state = [p for p in candidates if p.country == "US" and p.admin1 == state]
        if in_state:
            return in_state[0]
    if country is not None:
        in_country = [p for p in candidates if p.country == country]
        if in_country:
            return in_country[0]
    return candidates[0]


def resolve(text: str | None, *, _aliased: bool = False) -> Place | None:
    """The city a location text means, or None (see the module docstring)."""
    if not text or not str(text).strip():
        return None
    data = _data()
    whole = fold(str(text))
    parts = [fold(p) for p in _SPLIT.split(str(text)) if fold(p)]
    if not parts:
        return None
    for key in () if _aliased else (whole, parts[0]):
        alias = data["aliases"].get(key)
        if alias:
            return resolve(alias, _aliased=True)
    # the first part that names a city (a leading "Remote" or "Downtown" is not one), the parts
    # after it hinting which of that name's cities is meant
    for i, part in enumerate(parts):
        words = [w for w in part.split() if w not in data["noise"]]
        if words:
            found = _lookup(" ".join(words), parts[i + 1 :])
            if found is not None:
                return found
    return None


def miles(a: Place, b: Place) -> float:
    """The great-circle distance in miles."""
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp, dl = p2 - p1, math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_MILES * math.asin(math.sqrt(h))
