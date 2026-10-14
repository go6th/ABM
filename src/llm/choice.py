"""The user's model choice (owner decision 2026-10-14, Phase 4B section 1).

Two roles, from the ``choices`` block of ``config/models.yaml``:

* **research**: planning, pulling, extraction and classification (tiers planner, extractor,
  classifier); default Claude Opus 5.5;
* **analysis**: Ask, file analysis, the sense check, distilling, synthesis, narrative and the
  judge (tiers synthesis, narrative, judge); default Claude Fable 5.1.

``RunModels.for_tier`` keeps the judge independent of the generator (G12): when both roles name
the same model, the judge uses the other Claude model. ``availability`` says why a model cannot
be chosen ("Not set up" without its provider key, "Not enabled for your workspace" when the
platform has not enabled it or the client is not allowed it), so a choice never fails at Send.

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
    cat = Catalogue(
        models=tuple(models),
        research_tiers=frozenset(raw["research_tiers"]),
        analysis_tiers=frozenset(raw["analysis_tiers"]),
        default_research=str(raw["defaults"]["research"]),
        default_analysis=str(raw["defaults"]["analysis"]),
        needs_client_permission=frozenset(raw.get("needs_client_permission", [])),
    )
    for model_id in (cat.default_research, cat.default_analysis):
        if cat.get(model_id) is None:
            raise ValueError(f"models.yaml choices: default {model_id} is not a choice")
    return cat


@dataclass
class RunModels:
    """The models one run or answer uses, and any step that fell back (recorded with why)."""

    research: str
    analysis: str
    fallbacks: list[dict[str, str]] = field(default_factory=list)

    def for_tier(self, tier: str, cat: Catalogue | None = None) -> str:
        """The model for one tier of the legacy port."""
        cat = cat or catalogue()
        if tier in cat.research_tiers:
            return self.research
        if tier == "judge" and self.analysis == self.research:
            return independent_judge(self.research, cat)
        return self.analysis

    def fell_back(self, step: str, from_model: str, to_model: str, reason: str) -> None:
        """Record one step served by a different model than chosen."""
        self.fallbacks.append({"step": step, "from": from_model, "to": to_model, "reason": reason})

    def as_json(self) -> dict[str, Any]:
        """What the run row and the answer record store."""
        return {"research": self.research, "analysis": self.analysis, "fallbacks": self.fallbacks}

    @classmethod
    def from_json(cls, raw: Mapping[str, Any] | None) -> RunModels:
        """A stored record, or the defaults when there is none."""
        cat = catalogue()
        raw = raw or {}
        return cls(
            str(raw.get("research") or cat.default_research),
            str(raw.get("analysis") or cat.default_analysis),
            list(raw.get("fallbacks") or []),
        )


def independent_judge(generator: str, cat: Catalogue | None = None) -> str:
    """A Claude model other than ``generator`` for the judge (G12)."""
    cat = cat or catalogue()
    for model in cat.models:
        if model.provider == "anthropic" and model.id != generator:
            return model.id
    raise ValueError("no independent judge model is configured")


def claude_default(role: str, cat: Catalogue | None = None) -> str:
    """The Claude default of a role: where a Kimi step falls back when it cannot meet a schema."""
    cat = cat or catalogue()
    return cat.default_research if role == "research" else cat.default_analysis


def set_up(model: ModelChoice, env: Mapping[str, str] | None = None) -> bool:
    """Whether this process can call the model: its provider key, or the hermetic fake model."""
    env = os.environ if env is None else env
    if env.get("LLM_FAKE") == "hermetic":
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
