"""Load and validate the people configuration (fail fast) and hash it for the run record.

Files under ``CONFIG_DIR``: ``people_scope.yaml`` (PRD scope defaults), ``people_rubric.yaml``
(levels, bands, passes, exclusion and review cues, priority, counts definitions, caps) and
``job_lanes.yaml`` (the lane lexicon shared with the job pipeline; loaded through
``jobs.config`` so there is one implementation of the lane rules).

Guarantees: every rule and cap carries a ``source`` or ``status`` (a missing one is a
``ConfigError``); every level, exclusion, review and priority rule has a positive and a negative
example; the excluded terms of ``people_scope.yaml`` are exactly the cues of the exclusion rules;
``config_hash`` changes when any of the files changes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.db.enums import normalize_key
from jobs.config import load_jobs_config
from jobs.errors import ConfigError as JobsConfigError
from people.errors import ConfigError

PRIORITIES = ("A", "B", "C")


@dataclass(frozen=True)
class CueRule:
    """A whole-word cue rule: a level, an exclusion reason or a review reason."""

    rule_id: str
    value: str  # the level key, or the exclusion / review reason
    cues: tuple[str, ...]
    positive: str
    negative: str


@dataclass(frozen=True)
class PriorityRule:
    """First match wins: the band and whether a lane is required (``None`` = either)."""

    rule_id: str
    priority: str
    band: str
    lane_required: bool
    positive: str
    negative: str


@dataclass(frozen=True)
class LaneTerms:
    """One lane of the shared lexicon: platform names and aliases."""

    lane_id: str
    platforms: tuple[str, ...]
    terms: tuple[str, ...]


@dataclass(frozen=True)
class TenureBucket:
    """A tenure bucket in months: ``min_months <= m < max_months`` (``None`` = open)."""

    id: str
    min_months: int
    max_months: int | None


@dataclass(frozen=True)
class ProfileUrlRule:
    """How profile URLs are made canonical (proposal)."""

    canonical_host: str
    host_suffix: str
    fold_path_case: bool


@dataclass(frozen=True)
class PeopleConfig:
    """The validated configuration. Pure modules take the parts they need as arguments."""

    levels_in_scope: frozenset[str]
    level_order: tuple[str, ...]
    level_rules: tuple[CueRule, ...]
    band_of_level: dict[str, str]
    passes: tuple[tuple[int, str], ...]
    exclusion_rules: tuple[CueRule, ...]
    review_rules: tuple[CueRule, ...]
    lanes: tuple[LaneTerms, ...]
    priority_rules: tuple[PriorityRule, ...]
    top_n: int
    profile_levels: str
    ownership_titles: dict[str, tuple[str, ...]]
    leadership_titles: dict[str, tuple[str, ...]]
    title_fallback: str
    level_words: dict[str, tuple[str, ...]]
    association_functions: tuple[str, ...]
    technology_by_lane: dict[str, str]
    tenure_buckets: tuple[TenureBucket, ...]
    alumni_aliases: dict[str, tuple[str, ...]]
    profile_url: ProfileUrlRule
    allowed_profile_fields: frozenset[str]
    internal_only_providers: frozenset[str]
    geography: str
    caps: dict[str, float] = field(default_factory=dict)
    config_hash: str = ""

    def cap(self, name: str) -> int:
        """An integer cap (``ConfigError`` when absent)."""
        if name not in self.caps:
            raise ConfigError(f"cap {name} is not configured")
        return int(self.caps[name])

    def lane_ids(self) -> tuple[str, ...]:
        """All lane ids in lexicon order."""
        return tuple(lane.lane_id for lane in self.lanes)


def _load(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = yaml.safe_load(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot load configuration {path.name}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"configuration {path.name} must contain a mapping")
    return value, raw


def _provenance(entry: Any, where: str) -> None:
    if not isinstance(entry, dict) or not any(
        k in entry for k in ("source", "status", "functions_source", "aliases_source")
    ):
        raise ConfigError(f"{where} has no source or status")


def _examples(entry: dict[str, Any], where: str) -> tuple[str, str]:
    positive, negative = entry.get("positive"), entry.get("negative")
    if not positive or not negative:
        raise ConfigError(f"{where} needs a positive and a negative example")
    return str(positive), str(negative)


def _cue_rules(entries: list[dict[str, Any]], key: str, where: str) -> tuple[CueRule, ...]:
    rules = []
    for entry in entries:
        rule_id = str(entry.get("rule_id", ""))
        _provenance(entry, f"{where} {rule_id}")
        positive, negative = _examples(entry, f"{where} {rule_id}")
        cues = tuple(str(c).casefold() for c in entry.get("cues") or [])
        if not rule_id or not cues:
            raise ConfigError(f"{where} needs a rule_id and cues")
        rules.append(CueRule(rule_id, str(entry[key]), cues, positive, negative))
    return tuple(rules)


def _lanes(directory: Path) -> tuple[LaneTerms, ...]:
    try:
        jobs_cfg = load_jobs_config(directory)
    except JobsConfigError as exc:
        raise ConfigError(f"lane lexicon: {exc}") from exc
    lanes = []
    for lane in jobs_cfg.lanes:
        terms = [name for name in lane.platforms]
        terms.extend(alias for aliases in lane.platforms.values() for alias in aliases)
        lanes.append(LaneTerms(lane.id, tuple(lane.platforms), tuple(dict.fromkeys(terms))))
    return tuple(lanes)


def _tuple_map(value: Any) -> dict[str, tuple[str, ...]]:
    return {str(k): tuple(str(x) for x in (v or [])) for k, v in (value or {}).items()}


def _build(scope: dict[str, Any], rubric: dict[str, Any], directory: Path) -> PeopleConfig:
    for name in ("levels_in_scope", "excluded_title_terms", "merge_keys", "geography"):
        if name not in scope:
            raise ConfigError(f"people_scope.yaml has no {name}")
    _provenance(scope["geography"], "geography")
    levels = frozenset(normalize_key(str(level)) for level in scope["levels_in_scope"])
    order = tuple(str(x) for x in rubric["level_order"])
    level_rules = _cue_rules(rubric["level_rules"], "level", "level rule")
    unknown = {r.value for r in level_rules} - set(order)
    if unknown:
        raise ConfigError(f"level rules name levels outside level_order: {sorted(unknown)}")
    bands = rubric["level_bands"]
    _provenance(bands, "level_bands")
    band_of = {
        str(level): str(band) for band, ls in bands.items() if band != "status" for level in ls
    }
    if set(band_of) != levels:
        raise ConfigError("level_bands must cover exactly the levels in scope")
    _provenance(rubric["passes"], "passes")
    passes = tuple((int(p["pass"]), str(p["band"])) for p in rubric["passes"]["list"])
    if {b for _, b in passes} != set(band_of.values()):
        raise ConfigError("every band needs exactly one search pass")
    exclusions = _cue_rules(rubric["exclusion_rules"], "reason", "exclusion rule")
    excluded_terms = {str(t).casefold() for t in scope["excluded_title_terms"]}
    if {c for r in exclusions for c in r.cues} != excluded_terms:
        raise ConfigError("exclusion rule cues must equal people_scope.yaml excluded_title_terms")
    reviews = _cue_rules(rubric["review_rules"], "reason", "review rule")
    priorities = []
    for entry in rubric["priority_rules"]:
        _provenance(entry, f"priority rule {entry.get('rule_id')}")
        positive, negative = _examples(entry, f"priority rule {entry.get('rule_id')}")
        if entry["priority"] not in PRIORITIES or entry["band"] not in set(band_of.values()):
            raise ConfigError(f"priority rule {entry.get('rule_id')} has an unknown value")
        priorities.append(
            PriorityRule(
                str(entry["rule_id"]),
                str(entry["priority"]),
                str(entry["band"]),
                bool(entry["lane_required"]),
                positive,
                negative,
            )
        )
    top = rubric["top_people"]
    _provenance(top, "top_people")
    profile_levels = rubric["profile_levels"]
    _provenance(profile_levels, "profile_levels")
    titles = rubric["search_titles"]
    _provenance(titles, "search_titles")
    fallback = rubric["title_fallback"]
    _provenance(fallback, "title_fallback")
    if fallback["value"] not in ("platform_and_level", "none"):
        raise ConfigError("title_fallback must be platform_and_level or none")
    words = rubric["level_words"]
    _provenance(words, "level_words")
    association = rubric["association"]
    _provenance(association, "association")
    buckets_cfg = rubric["tenure_buckets"]
    _provenance(buckets_cfg, "tenure_buckets")
    buckets = tuple(
        TenureBucket(
            str(b["id"]),
            int(b["min_months"]),
            None if b["max_months"] is None else int(b["max_months"]),
        )
        for b in buckets_cfg["list"]
    )
    for left, right in zip(buckets, buckets[1:], strict=False):
        if left.max_months != right.min_months:
            raise ConfigError("tenure buckets must be contiguous")
    if not buckets or buckets[0].min_months != 0 or buckets[-1].max_months is not None:
        raise ConfigError("tenure buckets must start at 0 and end open")
    alumni = rubric["alumni"]
    _provenance(alumni, "alumni")
    certs = rubric["certifications"]
    _provenance(certs, "certifications")
    if certs["match"] != "lane_lexicon":
        raise ConfigError("certifications.match must be lane_lexicon")
    url = rubric["profile_url"]
    _provenance(url, "profile_url")
    fields = rubric["profile_fields"]
    _provenance(fields, "profile_fields")
    internal = rubric["internal_only_providers"]
    _provenance(internal, "internal_only_providers")
    caps: dict[str, float] = {}
    for name, cap in rubric["caps"].items():
        _provenance(cap, f"cap {name}")
        caps[str(name)] = float(cap["value"])
    return PeopleConfig(
        levels_in_scope=levels,
        level_order=order,
        level_rules=level_rules,
        band_of_level=band_of,
        passes=passes,
        exclusion_rules=exclusions,
        review_rules=reviews,
        lanes=_lanes(directory),
        priority_rules=tuple(priorities),
        top_n=int(top["n"]),
        profile_levels=str(profile_levels["value"]),
        ownership_titles=_tuple_map(titles.get("platform_ownership")),
        leadership_titles=_tuple_map(titles.get("leadership")),
        title_fallback=str(fallback["value"]),
        level_words={k: v for k, v in _tuple_map(words).items() if k != "status"},
        association_functions=tuple(str(f) for f in association["functions"]),
        technology_by_lane={
            str(k): str(v) for k, v in (association.get("technology_by_lane") or {}).items()
        },
        tenure_buckets=buckets,
        alumni_aliases=_tuple_map(alumni.get("aliases")),
        profile_url=ProfileUrlRule(
            str(url["canonical_host"]).lower(),
            str(url["host_suffix"]).lower(),
            str(url.get("path_case", "keep")) == "fold",
        ),
        allowed_profile_fields=frozenset(str(f) for f in fields["allowed"]),
        internal_only_providers=frozenset(str(p) for p in internal["value"]),
        geography=str(scope["geography"]["value"]),
        caps=caps,
    )


@lru_cache(maxsize=4)
def load_people_config(directory: Path | None = None) -> PeopleConfig:
    """Load and validate all people configuration; ``ConfigError`` on anything wrong."""
    base = directory or CONFIG_DIR
    scope, s_raw = _load(base / "people_scope.yaml")
    rubric, r_raw = _load(base / "people_rubric.yaml")
    lanes_raw = (base / "job_lanes.yaml").read_bytes()
    try:
        cfg = _build(scope, rubric, base)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigError(f"invalid people configuration: {exc!r}") from exc
    digest = hashlib.sha256(s_raw + b"\0" + r_raw + b"\0" + lanes_raw).hexdigest()
    from dataclasses import replace

    return replace(cfg, config_hash=digest)
