"""Run modes (D-102, G14 to G17): ``full`` (default), ``pull`` (Phase 1 only), ``build`` (Phase 2).

* The mode is a run attribute chosen only by an explicit typed field (start-run form, API
  ``mode``, scheduler). ``resolve_mode`` accepts ``None`` (meaning ``full``) or one of the three
  exact tokens; it never parses free text, folds case or reads model output.
* The modes are slices of one graph: ``full`` runs the pull slice then the build slice on its own
  manifest, exactly as a ``pull`` run followed by a ``build`` run would (G17).
* A ``build`` run is a child of a ``pull`` run and is pinned to its parent's skill config version
  unless a newer one is chosen explicitly; the choice and its reason are recorded.

GUARANTEE(test_no_mode_means_full)
GUARANTEE(test_mode_is_never_parsed_from_free_text)
GUARANTEE(test_build_pins_the_parent_config_version_unless_overridden)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from agents.errors import InvalidMode


class RunMode(StrEnum):
    """The three explicit modes."""

    FULL = "full"
    PULL = "pull"
    BUILD = "build"


# What the start-run form shows (work item 2); the stored value is the enum token.
MODE_LABELS: dict[RunMode, str] = {
    RunMode.FULL: "Run both (default)",
    RunMode.PULL: "Phase 1 only",
    RunMode.BUILD: "Phase 2 only",
}


def resolve_mode(value: object) -> RunMode:
    """``None`` is ``full``; an exact token is that mode; anything else is ``InvalidMode``."""
    if value is None:
        return RunMode.FULL
    if isinstance(value, RunMode):
        return value
    if isinstance(value, str) and value in {m.value for m in RunMode}:
        return RunMode(value)
    raise InvalidMode("mode must be omitted or one of full, pull, build")


@dataclass(frozen=True)
class RunPlan:
    """What a run will execute and what it is pinned to."""

    mode: RunMode
    runs_pull: bool
    runs_build: bool
    parent_run_id: UUID | None
    config_version: str
    config_version_reason: str  # current | inherited | explicit_override


def plan_run(
    mode: RunMode,
    *,
    parent_run_id: UUID | None,
    parent_mode: RunMode | None,
    parent_config_version: str | None,
    requested_config_version: str | None,
    current_config_version: str,
) -> RunPlan:
    """The slices to run and the config pin. A build needs a pull (or full) parent; the other
    modes refuse a parent (a pull is always a fresh Phase 1)."""
    if mode is RunMode.BUILD:
        if parent_run_id is None or parent_mode is None or parent_config_version is None:
            raise InvalidMode("a build run needs the pull run it builds on")
        if parent_mode is RunMode.BUILD:
            raise InvalidMode("a build run's parent must be a pull or full run")
        if requested_config_version is None:
            version, reason = parent_config_version, "inherited"
        else:
            version, reason = requested_config_version, "explicit_override"
        return RunPlan(mode, False, True, parent_run_id, version, reason)
    if parent_run_id is not None:
        raise InvalidMode(f"a {mode.value} run has no parent run")
    version = requested_config_version or current_config_version
    reason = "explicit_override" if requested_config_version else "current"
    return RunPlan(mode, True, mode is RunMode.FULL, None, version, reason)
