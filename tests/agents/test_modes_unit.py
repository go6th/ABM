"""Run modes (D-102): pure decisions."""

from __future__ import annotations

import pytest

from agents.errors import InvalidMode
from agents.modes import RunMode, plan_run, resolve_mode
from agents.schemas import RunRequest


def test_no_mode_means_full() -> None:
    assert resolve_mode(None) is RunMode.FULL
    assert RunRequest(account_name="A", domain="a.example").mode is None


@pytest.mark.parametrize("value", ["Phase 1 only", "PULL", " pull", "both", "pull and build", 1])
def test_mode_is_never_parsed_from_free_text(value: object) -> None:
    with pytest.raises(InvalidMode):
        resolve_mode(value)


def test_mode_cannot_come_from_model_output() -> None:
    # The model has no mode field anywhere; the request schema refuses unknown values.
    with pytest.raises(ValueError):
        RunRequest(account_name="A", domain="a.example", mode="phase1")  # type: ignore[arg-type]


# G15 -------------------------------------------------------------------------------------------


def test_build_pins_the_parent_config_version_unless_overridden() -> None:
    from uuid import uuid4

    parent = uuid4()
    inherited = plan_run(
        RunMode.BUILD,
        parent_run_id=parent,
        parent_mode=RunMode.PULL,
        parent_config_version="sk-old",
        requested_config_version=None,
        current_config_version="sk-new",
    )
    assert (inherited.config_version, inherited.config_version_reason) == ("sk-old", "inherited")
    chosen = plan_run(
        RunMode.BUILD,
        parent_run_id=parent,
        parent_mode=RunMode.FULL,
        parent_config_version="sk-old",
        requested_config_version="sk-new",
        current_config_version="sk-new",
    )
    assert (chosen.config_version, chosen.config_version_reason) == ("sk-new", "explicit_override")
    with pytest.raises(InvalidMode):
        plan_run(
            RunMode.BUILD,
            parent_run_id=None,
            parent_mode=None,
            parent_config_version=None,
            requested_config_version=None,
            current_config_version="x",
        )
    with pytest.raises(InvalidMode):
        plan_run(
            RunMode.BUILD,
            parent_run_id=parent,
            parent_mode=RunMode.BUILD,
            parent_config_version="a",
            requested_config_version=None,
            current_config_version="a",
        )
    with pytest.raises(InvalidMode):
        plan_run(
            RunMode.PULL,
            parent_run_id=parent,
            parent_mode=None,
            parent_config_version=None,
            requested_config_version=None,
            current_config_version="a",
        )
    full = plan_run(
        RunMode.FULL,
        parent_run_id=None,
        parent_mode=None,
        parent_config_version=None,
        requested_config_version=None,
        current_config_version="cur",
    )
    assert (full.runs_pull, full.runs_build, full.config_version_reason) == (True, True, "current")
