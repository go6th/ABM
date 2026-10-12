"""Technologies per lane, matched by name against the account's own tech stack.

Nothing maps a lane to a technology by hand. A lane's name is its label in ``config/lanes.yaml``
without the parenthetical (``"Salesforce (including field service ...)"`` -> ``Salesforce``); its
terms are the words of that name less joining words. A technology in the account's stack
(``org_tech_stack``) belongs to the lane when one of the lane's terms is a word of the
technology's name or slug. The catch-all lane (``other``) takes every technology no named lane
took. Each lane keeps its most used technologies first (``job_post_used_count``), at most
``limit`` of them, so every technology the account runs is searchable without an unbounded spend.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from typing import Any

import yaml

CATCH_ALL = "other"
_JOINING = frozenset({"and", "or", "the", "of", "for", "including", "such", "as", "with"})
_WORD = re.compile(r"[a-z0-9]+")


@lru_cache(maxsize=1)
def lane_names() -> Mapping[str, str]:
    """Lane id to its name: the ``config/lanes.yaml`` label before any parenthetical."""
    from account_intelligence.config import CONFIG_DIR

    lanes = yaml.safe_load((CONFIG_DIR / "lanes.yaml").read_text())["lanes"]
    return {str(lane["id"]): str(lane.get("label") or lane["id"]).split("(")[0] for lane in lanes}


def _words(text: str) -> frozenset[str]:
    return frozenset(_WORD.findall(text.lower()))


def lane_terms(name: str) -> frozenset[str]:
    """The words of a lane name that identify its technologies."""
    return _words(name) - _JOINING


def technologies_by_lane(
    lanes: Iterable[str],
    stack: Sequence[Any],
    limit: int,
    names: Mapping[str, str] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Technology slugs per lane from the stack records (``technology_name``,
    ``technology_slug``, ``job_post_used_count``), most used first, ``limit`` per lane."""
    names = lane_names() if names is None else names
    ranked: dict[str, int] = {}
    titles: dict[str, str] = {}
    for item in stack:
        slug = str(item.technology_slug)
        if slug and int(item.job_post_used_count) >= ranked.get(slug, -1):
            ranked[slug] = int(item.job_post_used_count)
            titles[slug] = str(item.technology_name)
    order = sorted(ranked, key=lambda s: (-ranked[s], s))
    wanted = list(dict.fromkeys(lanes))
    out: dict[str, tuple[str, ...]] = {}
    taken: set[str] = set()
    for lane in wanted:
        if lane == CATCH_ALL:
            continue
        terms = lane_terms(names.get(lane, lane))
        found = [s for s in order if terms & (_words(titles[s]) | _words(s.replace("-", " ")))]
        taken.update(found)
        out[lane] = tuple(found[: max(0, limit)])
    if CATCH_ALL in wanted:
        out[CATCH_ALL] = tuple([s for s in order if s not in taken][: max(0, limit)])
    return {lane: out[lane] for lane in wanted if lane in out}
