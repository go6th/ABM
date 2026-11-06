"""The data-class guard on the one live model path (rule R8 of the deleted model Router, rebuilt;
ADR-0133).

R8: a request that carries tenant or private data goes only to a model that keeps nothing (zero
data retention). The deleted Router declared it but never ran on the live path. Here it is a
config-driven check inside ``RunDeps.resolve_model``, made before any cached reply is looked up
and before any paid call:

* ``config/models.yaml`` ``data_class_guard``: ``"off"`` (default: behaviour unchanged),
  ``"warn"`` (log ``model.data_class_violation`` and make the call) or ``"enforce"`` (refuse it
  with ``DataClassRefused``; the research graph ends the run PARTIAL
  ``model_data_class_refused`` like any other terminal model failure, ``agents.graph``
  ``MODEL_FAILURES``);
* each model's ``data_classes_allowed`` (absent: ``[public_web]``, deny by default): the data
  classes it may receive. Only a model whose zero retention is verified may list ``tenant`` or
  ``private``.

The data class of a call: ``public_web`` by default; a run whose input is the client's uploaded
files (``run_input.input_mode == "file"``) sends ``tenant`` data on every call (``RunDeps``).

GUARANTEE(test_the_guard_is_off_by_default_and_changes_nothing)
GUARANTEE(test_enforce_refuses_before_any_paid_call)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from llm.errors import LlmError

DATA_CLASSES = ("public_web", "tenant", "private")
MODES = ("off", "warn", "enforce")
DEFAULT_ALLOWED = frozenset({"public_web"})


class DataClassRefused(LlmError):
    """The model may not receive this data class (``data_class_guard: enforce``); no call was
    made. Carries codes only, never the request."""

    def __init__(self, model: str, data_class: str) -> None:
        super().__init__(f"model {model} may not receive {data_class} data")
        self.model = model
        self.data_class = data_class


@dataclass(frozen=True)
class DataClassPolicy:
    """``data_class_guard`` and each model's ``data_classes_allowed``."""

    mode: str
    allowed: Mapping[str, frozenset[str]]

    def permits(self, model: str, data_class: str) -> bool:
        """Whether ``model`` may receive ``data_class`` (an unknown model: public web only)."""
        return data_class in self.allowed.get(model, DEFAULT_ALLOWED)


def parse(doc: Mapping[str, Any]) -> DataClassPolicy:
    """The policy of a parsed ``models.yaml`` (``ValueError`` on an unknown mode or class)."""
    mode = doc.get("data_class_guard", "off")
    if mode not in MODES:
        # an unquoted YAML off reads as False: say so instead of guessing
        raise ValueError(f"models.yaml data_class_guard must be one of {MODES} (quoted): {mode!r}")
    allowed: dict[str, frozenset[str]] = {}
    for model_id, profile in (doc.get("models") or {}).items():
        classes = (profile or {}).get("data_classes_allowed")
        if classes is None:
            continue
        unknown = set(classes) - set(DATA_CLASSES)
        if unknown:
            raise ValueError(f"models.yaml {model_id}: unknown data classes {sorted(unknown)}")
        allowed[str(model_id)] = frozenset(str(c) for c in classes)
    return DataClassPolicy(str(mode), allowed)


@lru_cache(maxsize=2)
def load_policy(path: Path | None = None) -> DataClassPolicy:
    """``config/models.yaml``'s policy, read once (through ``llm.choice``, the file's reader)."""
    from llm.choice import models_document

    return parse(models_document(str(path) if path is not None else None))


def check(policy: DataClassPolicy, model: str, data_class: str) -> str:
    """``ok``, ``warn`` (log and continue) or ``refuse``; never ``refuse`` while the guard is
    off. ``ValueError`` for a data class that does not exist (a caller's bug, at any mode)."""
    if data_class not in DATA_CLASSES:
        raise ValueError(f"unknown data class {data_class!r}")
    if policy.mode == "off" or policy.permits(model, data_class):
        return "ok"
    return "warn" if policy.mode == "warn" else "refuse"
