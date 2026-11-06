"""The user's model choice (owner decision 2026-10-14, Phase 4B section 1).

Two roles, from the ``choices`` block of ``config/models.yaml``:

* **research**: planning (which also runs the sense check and distill, ADR-0116), pulling,
  extraction and classification (tiers planner, extractor, classifier); default Claude Opus 5.5;
* **analysis**: Ask, synthesis, narrative and the judge (tiers synthesis, narrative, judge);
  default Claude Opus 5.5 (while the role keeps its default, the judge's route is Claude
  Fable 5.1).

Which tier belongs to which role is ``llm.route.TIERS`` (the one routing table). The effort a
user picks serves the judgment tiers of its role; extraction and classification keep their
configured effort (``llm.route``). ``RunModels.for_tier`` keeps the judge independent of the
generator (G12): when both roles name the same model, the judge uses another Claude model, and
``independent_judge`` raises ``JudgeNotIndependent`` when there is none (the run then fails at
its start, before any paid call, ADR-0118). ``availability`` says why a model cannot be chosen
("Not set up" without its provider key, "Not enabled for your workspace" when the platform has
not enabled it or the client is not allowed it), so a choice never fails at Send.

GUARANTEE(test_the_judge_never_equals_the_research_model)
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR

NOT_SET_UP = "Not set up"
NOT_ENABLED = "Not enabled for your workspace"


@dataclass(frozen=True)
class ModelChoice:
    """One model a user may choose."""

    id: str
    label: str
    name: str
    line: str
    provider: str
    key_env: str
    features: tuple[str, ...]


@dataclass(frozen=True)
class Catalogue:
    """The choosable models, the two roles' tiers and the defaults."""

    models: tuple[ModelChoice, ...]
    research_tiers: frozenset[str]
    analysis_tiers: frozenset[str]
    default_research: str
    default_analysis: str
    needs_client_permission: frozenset[str]
    # the efforts a user may pick for a role and the default of each role (owner decision
    # 2026-10-20: medium or high, for every model)
    efforts: tuple[str, ...] = ("medium", "high")
    default_research_effort: str = "medium"
    default_analysis_effort: str = "medium"

    def get(self, model_id: str) -> ModelChoice | None:
        """The choice with this id, if any."""
        return next((m for m in self.models if m.id == model_id), None)

    @property
    def ids(self) -> tuple[str, ...]:
        """Every choosable model id."""
        return tuple(m.id for m in self.models)


@lru_cache(maxsize=2)
def catalogue(path: str | None = None) -> Catalogue:
    """Read and check the ``choices`` block (every id must be a configured, allowed model)."""
    doc: dict[str, Any] = yaml.safe_load(Path(path or CONFIG_DIR / "models.yaml").read_text())
    raw = doc["choices"]
    allowed = {str(m) for m in doc.get("allowlist", [])}
    models = []
    for entry in raw["models"]:
        model_id = str(entry["id"])
        profile = doc["models"].get(model_id)
        if profile is None or model_id not in allowed:
            raise ValueError(f"models.yaml choices: {model_id} is not a configured, allowed model")
        models.append(
            ModelChoice(
                id=model_id,
                label=str(entry["label"]),
                name=str(entry["name"]),
                line=str(entry["line"]),
                provider=str(profile["provider"]),
                key_env=str(entry["key_env"]),
                features=tuple(str(f) for f in entry.get("features", [])),
            )
        )
    from llm.route import TIERS

    cat = Catalogue(
        models=tuple(models),
        research_tiers=frozenset(str(t) for t, r in TIERS.items() if r.role == "research"),
        analysis_tiers=frozenset(str(t) for t, r in TIERS.items() if r.role == "analysis"),
        default_research=str(raw["defaults"]["research"]),
        default_analysis=str(raw["defaults"]["analysis"]),
        needs_client_permission=frozenset(raw.get("needs_client_permission", [])),
        efforts=tuple(str(e) for e in raw.get("efforts") or ("medium", "high")),
        default_research_effort=str((raw.get("default_effort") or {}).get("research", "medium")),
        default_analysis_effort=str((raw.get("default_effort") or {}).get("analysis", "medium")),
    )
    known = {"low", "medium", "high", "xhigh", "max"}
    if not cat.efforts or any(e not in known for e in cat.efforts):
        raise ValueError(f"models.yaml choices: unknown efforts {cat.efforts}")
    for effort in (cat.default_research_effort, cat.default_analysis_effort):
        if effort not in cat.efforts:
            raise ValueError(f"models.yaml choices: default effort {effort!r} is not offered")
    for model_id in (cat.default_research, cat.default_analysis):
        if cat.get(model_id) is None:
            raise ValueError(f"models.yaml choices: default {model_id} is not a choice")
    return cat


@dataclass
class RunModels:
    """The models one run or answer uses, the effort the user chose for each role, and any step
    that fell back (recorded with why). An effort of None (a run from before the choice
    existed) leaves each task's configured effort."""

    research: str
    analysis: str
    fallbacks: list[dict[str, str]] = field(default_factory=list)
    research_effort: str | None = None
    analysis_effort: str | None = None

    def effort_for_tier(self, tier: str, cat: Catalogue | None = None) -> str | None:
        """The effort the user chose for the role this tier belongs to (None: not chosen, or a
        tier the user's effort never serves: extraction and classification keep their task's
        configured effort, ``llm.route.TIERS``)."""
        from llm.route import user_effort_applies

        if not user_effort_applies(tier):
            return None
        cat = cat or catalogue()
        return self.research_effort if tier in cat.research_tiers else self.analysis_effort

    def for_tier(self, tier: str, cat: Catalogue | None = None) -> str:
        """The model for one tier of the legacy port."""
        cat = cat or catalogue()
        if tier in cat.research_tiers:
            return self.research
        if tier == "judge" and self.analysis == self.research:
            return independent_judge(self.research, cat)
        return self.analysis

    def fell_back(
        self, step: str, from_model: str, to_model: str, reason: str, **costs: str
    ) -> dict[str, str]:
        """Record one step served by a different model than chosen; ``costs`` are its billing
        facts (``discarded_usd``, ``extra_usd``: decimal strings). Returns the entry, so the
        extra cost can be added once the fallback calls are paid."""
        entry = {"step": step, "from": from_model, "to": to_model, "reason": reason, **costs}
        self.fallbacks.append(entry)
        return entry

    def as_json(self) -> dict[str, Any]:
        """What the run row and the answer record store (the efforts only once chosen)."""
        out: dict[str, Any] = {
            "research": self.research,
            "analysis": self.analysis,
            "fallbacks": self.fallbacks,
        }
        if self.research_effort is not None:
            out["research_effort"] = self.research_effort
        if self.analysis_effort is not None:
            out["analysis_effort"] = self.analysis_effort
        return out

    @classmethod
    def from_json(cls, raw: Mapping[str, Any] | None) -> RunModels:
        """A stored record, or the defaults when there is none."""
        cat = catalogue()
        raw = raw or {}
        return cls(
            str(raw.get("research") or cat.default_research),
            str(raw.get("analysis") or cat.default_analysis),
            list(raw.get("fallbacks") or []),
            _effort(raw.get("research_effort"), cat),
            _effort(raw.get("analysis_effort"), cat),
        )


def _effort(value: Any, cat: Catalogue) -> str | None:
    """A stored effort when it is still offered, else None (the tasks' own effort)."""
    return str(value) if value is not None and str(value) in cat.efforts else None


def independent_judge(generator: str, cat: Catalogue | None = None) -> str:
    """A Claude model of the allow-list other than ``generator`` for the judge (G12);
    ``JudgeNotIndependent`` when there is none (never a judge on the generator's model)."""
    from llm.route import JudgeNotIndependent

    cat = cat or catalogue()
    for model in cat.models:
        if model.provider == "anthropic" and model.id != generator:
            return model.id
    raise JudgeNotIndependent("no Claude model other than the generator's is configured")


@lru_cache(maxsize=2)
def models_document(path: str | None = None) -> dict[str, Any]:
    """``config/models.yaml`` as parsed, read once (for policies kept in other modules, such as
    ``llm.data_class``, so the file has one reader). Callers must not mutate it."""
    doc: dict[str, Any] = yaml.safe_load(Path(path or CONFIG_DIR / "models.yaml").read_text())
    return doc


@lru_cache(maxsize=2)
def task_efforts(path: str | None = None) -> dict[str, str]:
    """The configured effort of each ``models.yaml`` task (``task_settings``): the default
    effort of every tier routed to it (``llm.route``)."""
    doc: dict[str, Any] = yaml.safe_load(Path(path or CONFIG_DIR / "models.yaml").read_text())
    return {
        str(task): str(knobs["effort"])
        for task, knobs in (doc.get("task_settings") or {}).items()
        if (knobs or {}).get("effort") is not None
    }


def claude_default(role: str, cat: Catalogue | None = None) -> str:
    """The Claude default of a role: where a Kimi step falls back when it cannot meet a schema."""
    cat = cat or catalogue()
    return cat.default_research if role == "research" else cat.default_analysis


# provider keys this process loaded from the secret store (the cloud reads ANTHROPIC_SECRET and
# MOONSHOT_SECRET from Secret Manager, never ANTHROPIC_API_KEY / MOONSHOT_API_KEY), by key_env
_LOADED_KEYS: set[str] = set()


def mark_set_up(key_env: str) -> None:
    """Record that the process holds the provider key a model's ``key_env`` names (the container
    calls this after it loaded the key from Secret Manager, or for Vertex AI, which needs none)."""
    _LOADED_KEYS.add(key_env)


def set_up(model: ModelChoice, env: Mapping[str, str] | None = None) -> bool:
    """Whether this process can call the model: its provider key (in the environment, a file, or
    loaded from the secret store), or the hermetic fake model."""
    env = os.environ if env is None else env
    if env.get("LLM_FAKE") == "hermetic":
        return True
    if model.key_env in _LOADED_KEYS:
        return True
    return bool(env.get(model.key_env) or env.get(f"{model.key_env}_FILE"))


def availability(
    model_id: str,
    *,
    enabled: Sequence[str],
    allowed: Sequence[str] | None,
    env: Mapping[str, str] | None = None,
    cat: Catalogue | None = None,
) -> str | None:
    """None when the model can be chosen, else the reason in the words the user sees."""
    cat = cat or catalogue()
    model = cat.get(model_id)
    if model is None:
        return NOT_ENABLED
    if not set_up(model, env):
        return NOT_SET_UP
    if model_id not in enabled:
        return NOT_ENABLED
    if allowed is not None and model_id not in allowed:
        return NOT_ENABLED
    if allowed is None and model_id in cat.needs_client_permission:
        return NOT_ENABLED
    return None
