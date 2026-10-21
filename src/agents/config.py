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
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR
from agents.errors import AgentConfigInvalid

SKILL_FILES = ("source", "run", "pull_plan", "conversions", "consent", "provider_routes")
STEPS_WITH_PULLS = frozenset({"pass1", "pass2", "people"})


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
    shelf_life_days: Mapping[str, int | None]
    consent_scope: str
    consent_label: str
    consent_text_version: str | None
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
    plan: dict[str, PlanRow] = {}
    for key, spec in plan_doc["rows"].items():
        if spec["step"] not in STEPS_WITH_PULLS:
            raise AgentConfigInvalid(f"pull_plan.{key}: unknown step {spec['step']!r}")
        if spec["need"] not in needs:
            raise AgentConfigInvalid(f"pull_plan.{key}: unknown route need {spec['need']!r}")
        plan[str(key)] = PlanRow(
            str(key),
            str(spec["step"]),
            str(spec["need"]),
            bool(spec.get("people", False)),
            bool(spec.get("internal_history", False)),
            row_queries.get(str(key), ()),
        )
    if set(row_queries) - set(plan):
        raise AgentConfigInvalid(
            f"pull_plan.queries: unknown rows {sorted(set(row_queries) - set(plan))}"
        )
    if set(plan) != set(labels):
        unplanned, unknown = sorted(set(labels) - set(plan)), sorted(set(plan) - set(labels))
        raise AgentConfigInvalid(
            f"pull plan and checklist disagree: unplanned={unplanned}, unknown={unknown}"
        )
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
        shelf_life_days=shelf_days,
        consent_scope=str(_val(consent["people_consent"]["scope"], "consent.scope")),
        consent_label=str(_val(consent["people_consent"]["tenant_label"], "consent.label")),
        consent_text_version=_val(
            consent["people_consent"]["current_text_version"], "consent.text_version"
        ),
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
