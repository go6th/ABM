"""Load, validate and hash the skill content kept as config (D-99, D-101, work item 1).

Files: ``config/skill/{source,run,pull_plan,conversions,consent}.yaml``, ``config/agents.yaml``,
``config/lessons.yaml`` and ``config/skill_versions.yaml``, cross-checked against the platform's
``config/checklist.yaml`` (the coverage rows) and ``config/routing.yaml`` (the route needs).

Rules enforced at load time:

* every mapping that carries a ``value`` also carries a ``source`` or a ``status`` (no unsourced
  value; unknown is ``null`` with a status, never a guessed default);
* the pull plan names every checklist row exactly once and only known route needs (G1);
* skill updates go through the repository: ``in_app_repackaging`` must be false (D-101);
* the config version is a hash of the canonical content, pinned on every run (G17).

GUARANTEE(test_every_config_value_has_a_source_or_status)
GUARANTEE(test_pull_plan_covers_every_checklist_row)
GUARANTEE(test_config_version_changes_iff_content_changes)
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR
from agents.errors import AgentConfigInvalid

SKILL_FILES = (
    "scope",
    "source",
    "run",
    "pull_plan",
    "conversions",
    "consent",
    "provider_routes",
    "attributes",
)
STEPS_WITH_PULLS = frozenset({"pass1", "pass2", "people"})


@dataclass(frozen=True)
class FollowRule:
    """Links of the account's own pages that a row follows after reading them
    (``pull_plan.yaml`` ``follow``; ``agents.production.links``)."""

    # words a followed link's path must carry (a newsroom row: news, press, release)
    include: tuple[str, ...]
    # followed pages read per query of the row (each is a live scrape, one provider credit)
    per_query: int
    # the scope window (``scope.yaml`` windows_months key) a dated link must fall inside
    window: str | None = None


@dataclass(frozen=True)
class AlsoOn:
    """Other places a row's query is searched, beyond the open web and the account's own site
    (``pull_plan.yaml`` ``also_on``): a public register (filings) or the newswires that carry the
    company's releases. The search is restricted to ``domains`` and the first ``reads`` pages it
    finds there are read in full."""

    domains: tuple[str, ...]
    reads: int
    # the scope window (``scope.yaml`` windows_months key) the search is bounded to (its results
    # must be newer than the window), None for no bound
    window: str | None = None


@dataclass(frozen=True)
class PlanRow:
    """How Phase 1 pulls one coverage row."""

    row_key: str
    step: str
    need: str
    people: bool
    internal_history: bool
    # the row's own search queries (``pull_plan.yaml`` queries); empty: the generic phrasings
    queries: tuple[str, ...] = ()
    # the row's queries are also searched on the account's own site and the pages found there
    # are read in full (the primary-source path to Confirmed, 2026-10-24)
    own_site: bool = False
    # a code pipeline owns the row after Phase 1 ("jobs": the job pipeline reads and classifies
    # the postings; "tech": detections are written as dated rows); the extractor skips it
    pipeline: str | None = None
    # the own-site links the row follows after its reads (None: none)
    follow: FollowRule | None = None
    # the extra restricted search of the row's query (None: none)
    also_on: AlsoOn | None = None


@dataclass(frozen=True)
class AttributeSpec:
    """One named attribute of the vocabulary (``config/skill/attributes.yaml``)."""

    attribute: str
    row_key: str
    meaning: str
    tier: int


@dataclass(frozen=True)
class Conversion:
    """One SaaS conversion of an operator-in-the-loop part of the skill (guide S15)."""

    id: str
    kind: str
    source: str
    decision: str | None
    enforced_by: str
    test: str


@dataclass(frozen=True)
class Scope:
    """The fixed research scope (``config/skill/scope.yaml``: master prompt v2 section 0)."""

    lanes: tuple[str, ...]
    also_tracked: tuple[str, ...]
    never_report: tuple[str, ...]
    never_report_mask: str
    window_months: Mapping[str, int]  # news, postings, detections
    seniority_rule: str
    excluded_title_patterns: tuple[str, ...]
    lookalike_edit_distance: int
    name_stopwords: tuple[str, ...]
    min_brand_mentions: int
    lead_min_queries: int
    lead_min_tools: int


@dataclass(frozen=True)
class SkillConfig:
    """The validated skill content and its version hash."""

    skill_version: str
    archive_status: str
    steps: tuple[str, ...]
    min_phrasings: int
    min_tool_categories: int
    sense_kinds: tuple[str, ...]
    follow_up_rounds: Mapping[str, int]
    lanes: tuple[str, ...]
    salesforce_includes: tuple[str, ...]
    phrasings: tuple[str, ...]
    plan: Mapping[str, PlanRow]
    row_labels: Mapping[str, str]
    # the attribute vocabulary: attribute name -> spec (its canonical row, meaning, tier), and
    # the attribute names listed with every extraction call
    attributes: Mapping[str, AttributeSpec]
    shared_attributes: tuple[str, ...]
    shelf_life_days: Mapping[str, int | None]
    consent_scope: str
    consent_label: str
    consent_text: str
    consent_text_version: str | None
    scope: Scope
    internal_history_default: bool
    conversions: tuple[Conversion, ...]
    update_channel: str
    lessons: tuple[Mapping[str, Any], ...]
    lessons_archive_status: str
    config_version: str


# the words a row's query may name (filled in code: never model text, never a user's words)
QUERY_FIELDS = frozenset({"account", "domain", "year", "prev_year"})


def _row_queries(plan_doc: Mapping[str, Any], min_phrasings: int) -> dict[str, tuple[str, ...]]:
    """``pull_plan.yaml`` queries: per row, at least ``min_phrasings`` templates (a lead is
    dropped only after three phrasings, G3) naming only ``QUERY_FIELDS``."""
    import string

    section = plan_doc.get("queries")
    if section is None:
        return {}
    out: dict[str, tuple[str, ...]] = {}
    for key, templates in _val(section, "queries").items():
        items = tuple(str(t) for t in templates)
        if len(set(items)) < min_phrasings:
            raise AgentConfigInvalid(f"pull_plan.queries.{key}: fewer than {min_phrasings}")
        for template in items:
            names = {f for _, f, _, _ in string.Formatter().parse(template) if f is not None}
            if names - QUERY_FIELDS or not names & {"account", "domain"}:
                raise AgentConfigInvalid(f"pull_plan.queries.{key}: bad template {template!r}")
        out[str(key)] = items
    return out


FOLLOW_WINDOWS = frozenset({"news", "postings", "detections"})
MAX_FOLLOW_PER_QUERY = 20


def _follow_rules(plan_doc: Mapping[str, Any]) -> dict[str, FollowRule]:
    """``pull_plan.yaml`` ``follow``: per own-site row, the words a followed link's path carries,
    how many links a query follows and the window a dated link must fall inside."""
    section = plan_doc.get("follow")
    if section is None:
        return {}
    out: dict[str, FollowRule] = {}
    for key, spec in _val(section, "follow").items():
        include = tuple(str(w).strip().lower() for w in spec["include"])
        per_query = int(spec["per_query"])
        window = spec.get("window")
        if not include or any(not w for w in include):
            raise AgentConfigInvalid(f"pull_plan.follow.{key}: no words")
        if not 1 <= per_query <= MAX_FOLLOW_PER_QUERY:
            raise AgentConfigInvalid(f"pull_plan.follow.{key}: per_query out of range")
        if window is not None and str(window) not in FOLLOW_WINDOWS:
            raise AgentConfigInvalid(f"pull_plan.follow.{key}: unknown window {window!r}")
        out[str(key)] = FollowRule(include, per_query, str(window) if window else None)
    return out


_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
MAX_ALSO_ON_DOMAINS = 10
MAX_ALSO_ON_READS = 6


def _also_on_rules(plan_doc: Mapping[str, Any]) -> dict[str, AlsoOn]:
    """``pull_plan.yaml`` ``also_on``: per row, the plain host names its query is also searched on,
    how many of the pages found there are read and the window bounding the search."""
    section = plan_doc.get("also_on")
    if section is None:
        return {}
    out: dict[str, AlsoOn] = {}
    for key, spec in _val(section, "also_on").items():
        domains = tuple(str(d).strip().lower() for d in spec["domains"])
        reads = int(spec["reads"])
        window = spec.get("window")
        if not domains or len(domains) > MAX_ALSO_ON_DOMAINS:
            raise AgentConfigInvalid(f"pull_plan.also_on.{key}: bad domain list")
        if any(not _HOSTNAME.match(d) for d in domains):
            raise AgentConfigInvalid(f"pull_plan.also_on.{key}: a domain is not a plain host name")
        if not 1 <= reads <= MAX_ALSO_ON_READS:
            raise AgentConfigInvalid(f"pull_plan.also_on.{key}: reads out of range")
        if window is not None and str(window) not in FOLLOW_WINDOWS:
            raise AgentConfigInvalid(f"pull_plan.also_on.{key}: unknown window {window!r}")
        out[str(key)] = AlsoOn(domains, reads, str(window) if window else None)
    return out


PIPELINES = frozenset({"jobs", "tech", "people"})
_ATTRIBUTE = re.compile(r"^[a-z][a-z0-9_]{1,60}$")


def _attributes(
    doc: Mapping[str, Any], plan_rows: Iterable[str]
) -> tuple[dict[str, AttributeSpec], tuple[str, ...]]:
    """``attributes.yaml``: every attribute name is unique across rows (it decides the canonical
    row), names a planned row, and has a meaning and a tier of 0 to 3; ``shared`` names only
    listed attributes."""
    rows = set(plan_rows)
    out: dict[str, AttributeSpec] = {}
    for row_key, attrs in _val(doc["rows"], "attributes.rows").items():
        if str(row_key) not in rows:
            raise AgentConfigInvalid(f"attributes.rows: unknown row {row_key!r}")
        for name, spec in (attrs or {}).items():
            if not _ATTRIBUTE.match(str(name)):
                raise AgentConfigInvalid(f"attributes.rows.{row_key}: bad name {name!r}")
            if str(name) in out:
                raise AgentConfigInvalid(f"attributes.rows: {name!r} listed under two rows")
            meaning = str(spec.get("meaning") or "").strip()
            tier = int(spec.get("tier", -1))
            if not meaning or tier not in (0, 1, 2, 3):
                raise AgentConfigInvalid(f"attributes.rows.{row_key}.{name}: needs meaning, tier")
            out[str(name)] = AttributeSpec(str(name), str(row_key), meaning, tier)
    shared = tuple(str(a) for a in _val(doc["shared"], "attributes.shared"))
    unknown = sorted(set(shared) - set(out))
    if unknown:
        raise AgentConfigInvalid(f"attributes.shared: unknown attributes {unknown}")
    return out, shared


def _read(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as error:
        raise AgentConfigInvalid(f"{path.name}: unreadable") from error


def unsourced_values(tree: Any, path: str = "") -> list[str]:
    """Paths of every mapping holding a ``value`` with neither ``source`` nor ``status``."""
    found: list[str] = []
    if isinstance(tree, Mapping):
        if "value" in tree and not ({"source", "status"} & tree.keys()):
            found.append(path or "<root>")
        for key, nested in tree.items():
            found.extend(unsourced_values(nested, f"{path}.{key}" if path else str(key)))
    elif isinstance(tree, list):
        for index, nested in enumerate(tree):
            found.extend(unsourced_values(nested, f"{path}[{index}]"))
    return found


def content_hash(documents: Mapping[str, Any]) -> str:
    """``sk-`` plus the first 16 hex digits of sha256 over the canonical JSON of ``documents``."""
    canonical = json.dumps(documents, sort_keys=True, separators=(",", ":"), default=str)
    return "sk-" + hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _scope(doc: Mapping[str, Any]) -> Scope:
    """Validate ``scope.yaml`` (every list non-empty, windows positive, the mask plain text)."""
    try:
        never = tuple(str(x) for x in _val(doc["never_report"], "scope.never_report"))
        mask = str(doc["never_report"].get("mask") or "").strip()
        windows = {
            k: int(_val(doc["windows_months"][k], f"scope.windows_months.{k}"))
            for k in ("news", "postings", "detections")
        }
        out = Scope(
            lanes=tuple(str(x) for x in _val(doc["lanes"], "scope.lanes")),
            also_tracked=tuple(str(x) for x in _val(doc["also_tracked"], "scope.also_tracked")),
            never_report=never,
            never_report_mask=mask,
            window_months=windows,
            seniority_rule=str(_val(doc["seniority"]["rule"], "scope.seniority.rule")),
            excluded_title_patterns=tuple(
                str(x).lower()
                for x in _val(doc["people_exclusions"]["title_patterns"], "scope.exclusions")
            ),
            lookalike_edit_distance=int(
                _val(doc["entity"]["lookalike_edit_distance"], "scope.entity.distance")
            ),
            name_stopwords=tuple(
                str(x).lower() for x in _val(doc["entity"]["name_stopwords"], "scope.stopwords")
            ),
            min_brand_mentions=int(
                _val(doc["entity"]["min_brand_mentions"], "scope.entity.min_brand_mentions")
            ),
            lead_min_queries=int(_val(doc["lead_verification"]["min_queries"], "scope.lead")),
            lead_min_tools=int(_val(doc["lead_verification"]["min_tools"], "scope.lead")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise AgentConfigInvalid(f"scope.yaml: {error}") from error
    if not (out.lanes and out.never_report and mask and out.name_stopwords):
        raise AgentConfigInvalid("scope.yaml: lanes, never_report, mask and stopwords are needed")
    if any(v <= 0 for v in windows.values()) or out.lookalike_edit_distance < 1:
        raise AgentConfigInvalid("scope.yaml: windows and the edit distance must be positive")
    return out


def _val(block: Any, name: str) -> Any:
    if not isinstance(block, Mapping) or "value" not in block:
        raise AgentConfigInvalid(f"{name}: expected a mapping with a value")
    return block["value"]


def load_skill_config(config_dir: Path | None = None) -> SkillConfig:
    """Read, validate and hash the skill config (see the module docstring)."""
    base = config_dir or CONFIG_DIR
    docs: dict[str, Any] = {name: _read(base / "skill" / f"{name}.yaml") for name in SKILL_FILES}
    docs["agents"] = _read(base / "agents.yaml")
    docs["lessons"] = _read(base / "lessons.yaml")
    docs["skill_versions"] = _read(base / "skill_versions.yaml")
    unsourced = [f"{name}:{p}" for name, doc in docs.items() for p in unsourced_values(doc)]
    if unsourced:
        raise AgentConfigInvalid(f"values without source or status: {sorted(unsourced)}")
    checklist = _read(base / "checklist.yaml")
    routing = _read(base / "routing.yaml")
    labels = {str(r["key"]): str(r["row"]) for r in checklist["rows"]}
    shelf = {name: spec.get("days") for name, spec in checklist["shelf_lives"].items()}
    shelf_days = {str(r["key"]): shelf.get(r.get("shelf_life", "unset")) for r in checklist["rows"]}
    needs = {str(route["need"]) for route in routing["routes"]}

    run, plan_doc = docs["run"], docs["pull_plan"]
    min_phrasings = int(_val(run["phrasing"]["min_phrasings"], "min_phrasings"))
    row_queries = _row_queries(plan_doc, min_phrasings)
    follow = _follow_rules(plan_doc)
    also_on = _also_on_rules(plan_doc)
    plan: dict[str, PlanRow] = {}
    for key, spec in plan_doc["rows"].items():
        if spec["step"] not in STEPS_WITH_PULLS:
            raise AgentConfigInvalid(f"pull_plan.{key}: unknown step {spec['step']!r}")
        if spec["need"] not in needs:
            raise AgentConfigInvalid(f"pull_plan.{key}: unknown route need {spec['need']!r}")
        pipeline = spec.get("pipeline")
        if pipeline is not None and str(pipeline) not in PIPELINES:
            raise AgentConfigInvalid(f"pull_plan.{key}: unknown pipeline {pipeline!r}")
        plan[str(key)] = PlanRow(
            str(key),
            str(spec["step"]),
            str(spec["need"]),
            bool(spec.get("people", False)),
            bool(spec.get("internal_history", False)),
            row_queries.get(str(key), ()),
            bool(spec.get("own_site", False)),
            str(pipeline) if pipeline is not None else None,
            follow.get(str(key)),
            also_on.get(str(key)),
        )
    for key in also_on:
        if key not in plan or plan[key].pipeline is not None or plan[key].people:
            raise AgentConfigInvalid(f"pull_plan.also_on.{key}: not a web row")
    for key in follow:
        if key not in plan or not plan[key].own_site:
            raise AgentConfigInvalid(f"pull_plan.follow.{key}: not an own_site row")
    if set(row_queries) - set(plan):
        raise AgentConfigInvalid(
            f"pull_plan.queries: unknown rows {sorted(set(row_queries) - set(plan))}"
        )
    if set(plan) != set(labels):
        unplanned, unknown = sorted(set(labels) - set(plan)), sorted(set(plan) - set(labels))
        raise AgentConfigInvalid(
            f"pull plan and checklist disagree: unplanned={unplanned}, unknown={unknown}"
        )
    attributes, shared = _attributes(docs["attributes"], plan)
    versions = docs["skill_versions"]
    if _val(versions["in_app_repackaging"], "in_app_repackaging") is not False:
        raise AgentConfigInvalid("skill updates must go through the repository (D-101)")
    consent = docs["consent"]
    min_tools = int(_val(run["phrasing"]["min_tool_categories"], "min_tool_categories"))
    if min_phrasings < 3 or min_tools < 1:
        raise AgentConfigInvalid("phrasing minimums below the skill's three phrasings (G3)")
    lessons_doc = docs["lessons"]
    return SkillConfig(
        skill_version=str(_val(docs["source"]["skill"]["version"], "skill.version")),
        archive_status=str(docs["source"]["archive"]["status"]),
        steps=tuple(str(s["id"]) for s in _val(run["steps"], "steps")),
        min_phrasings=min_phrasings,
        min_tool_categories=min_tools,
        sense_kinds=tuple(_val(run["sense_check"]["kinds"], "sense_check.kinds")),
        follow_up_rounds={
            str(depth): max(1, int(rounds))
            for depth, rounds in _val(
                run["sense_check"]["follow_up_rounds"], "sense_check.follow_up_rounds"
            ).items()
        },
        lanes=tuple(_val(run["lanes"]["description_lanes"], "lanes")),
        salesforce_includes=tuple(_val(run["lanes"]["salesforce_includes"], "salesforce")),
        phrasings=tuple(str(p) for p in _val(plan_doc["phrasings"], "phrasings")),
        plan=plan,
        row_labels=labels,
        attributes=attributes,
        shared_attributes=shared,
        shelf_life_days=shelf_days,
        consent_scope=str(_val(consent["people_consent"]["scope"], "consent.scope")),
        consent_label=str(_val(consent["people_consent"]["tenant_label"], "consent.label")),
        consent_text=" ".join(str(_val(consent["people_consent"]["text"], "consent.text")).split()),
        consent_text_version=_val(
            consent["people_consent"]["current_text_version"], "consent.text_version"
        ),
        scope=_scope(docs["scope"]),
        internal_history_default=bool(
            _val(consent["internal_history"]["default_enabled"], "internal_history")
        ),
        conversions=tuple(
            Conversion(
                id=str(c["id"]),
                kind=str(c["saas"]["kind"]),
                source=str(c["skill"]["source"]),
                decision=c["saas"].get("decision"),
                enforced_by=str(c["enforced_by"]),
                test=str(c["test"]),
            )
            for c in docs["conversions"]["conversions"]
        ),
        update_channel=str(_val(versions["update_channel"], "update_channel")),
        lessons=tuple(lessons_doc.get("lessons") or ()),
        lessons_archive_status=str(lessons_doc.get("archive_status", "unknown")),
        config_version=content_hash(docs),
    )


@lru_cache(maxsize=1)
def skill_config() -> SkillConfig:
    """The process-wide validated config (read once; startup fails on an invalid file)."""
    return load_skill_config()
