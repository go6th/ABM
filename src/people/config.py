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
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.db.enums import normalize_key
from account_intelligence.dev_limits import UNCAPPED, research_uncapped
from jobs.config import load_jobs_config
from jobs.errors import ConfigError as JobsConfigError
from people.errors import ConfigError

# caps that bound how many people, profiles and posts are read: lifted by DEV_UNCAPPED (dev only)
_BREADTH_CAPS = (
    "max_people_per_run",
    "max_pages_per_source",
    "max_profiles_per_run",
    "max_posts_per_person",
    "mention_queries_per_person",
    "mention_run_cap",
    "max_extractor_calls",
)

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
class TitleRule:
    """An ordered title rule (the People List's lane and priority): whole-word ``cues`` and
    regular-expression ``patterns`` that fire it, and the ``unless_*`` words that switch it off.
    ``value`` is the lane id or the priority letter. First matching rule in the list wins."""

    rule_id: str
    value: str
    cues: tuple[str, ...]
    patterns: tuple[str, ...]
    unless_cues: tuple[str, ...]
    positive: str
    negative: str


@dataclass(frozen=True)
class SiFirm:
    """A systems-integrator or consulting firm and the regular-expression fragments that match
    its name in a profile's employer field."""

    firm: str
    aliases: tuple[str, ...]


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
    """How profile URLs are made canonical (proposal). ``person_paths`` are the first path
    segments of a person's own page on the profile host (``/in/<slug>`` and the older public
    ``/pub/<name>/..`` form); any other page there (a company, a post, a job) identifies nobody."""

    canonical_host: str
    host_suffix: str
    fold_path_case: bool
    person_paths: tuple[str, ...] = ("in", "pub")


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
    priority_rules: tuple[TitleRule, ...]
    top_n: int
    profile_levels: str
    ownership_titles: dict[str, tuple[str, ...]]
    leadership_titles: dict[str, tuple[str, ...]]
    title_fallback: str
    level_words: dict[str, tuple[str, ...]]
    association_functions: tuple[str, ...]
    technologies_per_lane: int
    tenure_buckets: tuple[TenureBucket, ...]
    alumni_aliases: dict[str, tuple[str, ...]]
    profile_url: ProfileUrlRule
    allowed_profile_fields: frozenset[str]
    internal_only_providers: frozenset[str]
    geography: str
    caps: dict[str, float] = field(default_factory=dict)
    config_hash: str = ""
    # the default level of a title with no level cue, and the rule that names it
    default_level: str = "other"
    default_level_rule: str = "level.default"
    # levels below the seniority rule (band ``below``): listed only when the title names a
    # platform or technology (``stack_terms`` plus the lane lexicon's platform names)
    below_levels: frozenset[str] = frozenset()
    stack_terms: tuple[str, ...] = ()
    # the People List's one lane per person: ordered rules and the default lane
    role_lanes: tuple[TitleRule, ...] = ()
    role_lane_default: str = ""
    role_lane_default_rule: str = ""
    priority_default: str = "B"
    priority_default_rule: str = "prio.b.default"
    si_firms: tuple[SiFirm, ...] = ()

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


def _title_rules(entries: list[dict[str, Any]], key: str, where: str) -> tuple[TitleRule, ...]:
    """Ordered title rules (lane or priority): cues and patterns, an optional ``unless_cues``
    list, provenance and a positive and a negative example. A bad pattern is a ``ConfigError``."""
    rules = []
    for entry in entries:
        rule_id = str(entry.get("rule_id", ""))
        _provenance(entry, f"{where} {rule_id}")
        positive, negative = _examples(entry, f"{where} {rule_id}")
        cues = tuple(str(c).casefold() for c in entry.get("cues") or [])
        patterns = tuple(str(p) for p in entry.get("patterns") or [])
        unless = tuple(str(c).casefold() for c in entry.get("unless_cues") or [])
        if not rule_id or not (cues or patterns):
            raise ConfigError(f"{where} needs a rule_id and cues or patterns")
        for fragment in (*patterns, *(entry.get("aliases") or [])):
            try:
                re.compile(fragment)
            except re.error as exc:
                raise ConfigError(f"{where} {rule_id}: bad pattern {fragment!r}") from exc
        rules.append(
            TitleRule(rule_id, str(entry[key]), cues, patterns, unless, positive, negative)
        )
    return tuple(rules)


def _lanes(directory: Path) -> tuple[LaneTerms, ...]:
    try:
        jobs_cfg = load_jobs_config(directory)
    except JobsConfigError as exc:
        raise ConfigError(f"lane lexicon: {exc}") from exc
    lanes = []
    for lane in jobs_cfg.lanes:
        if not lane.people_search:
            continue  # counted in the job lexicon only (Workday): no people title search
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
    below = frozenset(str(x) for x in bands.get("below") or ())
    if set(band_of) - below != levels:
        raise ConfigError("level_bands must cover exactly the levels in scope (and the below band)")
    if below & levels:
        raise ConfigError("a level in scope cannot be in the below band")
    if set(band_of) != set(order):
        raise ConfigError("level_bands must give every level of level_order exactly one band")
    default = rubric["default_level"]
    _provenance(default, "default_level")
    default_level = str(default["value"])
    if default_level not in below or default_level not in order:
        raise ConfigError("default_level must be a level of the below band in level_order")
    _provenance(rubric["passes"], "passes")
    passes = tuple((int(p["pass"]), str(p["band"])) for p in rubric["passes"]["list"])
    if {b for _, b in passes} != {b for b in set(band_of.values()) if b != "below"}:
        raise ConfigError("every band needs exactly one search pass")
    exclusions = _cue_rules(rubric["exclusion_rules"], "reason", "exclusion rule")
    excluded_terms = {str(t).casefold() for t in scope["excluded_title_terms"]}
    if {c for r in exclusions for c in r.cues} != excluded_terms:
        raise ConfigError("exclusion rule cues must equal people_scope.yaml excluded_title_terms")
    reviews = _cue_rules(rubric["review_rules"], "reason", "review rule")
    priorities = _title_rules(rubric["priority_rules"], "priority", "priority rule")
    if {r.value for r in priorities} - set(PRIORITIES):
        raise ConfigError("priority rules have an unknown priority")
    prio_default = rubric["priority_default"]
    _provenance(prio_default, "priority_default")
    if str(prio_default["priority"]) not in PRIORITIES:
        raise ConfigError("priority_default has an unknown priority")
    lane_block = rubric["people_lanes"]
    _provenance(lane_block, "people_lanes")
    role_lanes = _title_rules(lane_block["list"], "lane", "people lane")
    lane_default = lane_block["default"]
    _provenance(lane_default, "people_lanes default")
    if len({r.value for r in role_lanes}) != len(role_lanes):
        raise ConfigError("people_lanes: a lane may have one rule only")
    stack = rubric["people_stack_terms"]
    _provenance(stack, "people_stack_terms")
    stack_terms = tuple(dict.fromkeys(str(t).casefold() for t in stack["list"]))
    if not stack_terms:
        raise ConfigError("people_stack_terms is empty")
    firms_block = rubric["si_firms"]
    _provenance(firms_block, "si_firms")
    firms = tuple(
        SiFirm(str(f["firm"]), tuple(str(a) for a in f["aliases"])) for f in firms_block["list"]
    )
    if not firms or any(not f.firm or not f.aliases for f in firms):
        raise ConfigError("si_firms needs a firm name and aliases for every firm")
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
    if research_uncapped():  # caps off: every person, profile and post (dev_limits)
        caps.update({k: float(UNCAPPED) for k in _BREADTH_CAPS})
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
        technologies_per_lane=(
            UNCAPPED if research_uncapped() else int(association["technologies_per_lane"])
        ),
        tenure_buckets=buckets,
        alumni_aliases=_tuple_map(alumni.get("aliases")),
        profile_url=ProfileUrlRule(
            str(url["canonical_host"]).lower(),
            str(url["host_suffix"]).lower(),
            str(url.get("path_case", "keep")) == "fold",
            tuple(str(p).strip("/").casefold() for p in url.get("person_paths") or ("in", "pub")),
        ),
        allowed_profile_fields=frozenset(str(f) for f in fields["allowed"]),
        internal_only_providers=frozenset(str(p) for p in internal["value"]),
        geography=str(scope["geography"]["value"]),
        caps=caps,
        default_level=default_level,
        default_level_rule=str(default["rule_id"]),
        below_levels=below,
        stack_terms=stack_terms,
        role_lanes=role_lanes,
        role_lane_default=str(lane_default["lane"]),
        role_lane_default_rule=str(lane_default["rule_id"]),
        priority_default=str(prio_default["priority"]),
        priority_default_rule=str(prio_default["rule_id"]),
        si_firms=firms,
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
