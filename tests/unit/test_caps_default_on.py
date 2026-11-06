"""The research caps and the token guard are ON by default in every cloud profile (fix prompt A,
2026-11-03): staging ran with ``RESEARCH_CAPS=off`` from 2026-10-21, which lifts the dollar cap,
the per-agent budgets, the repeat-call limit, the slice ceiling and the per-user research limit.

The Terraform files are read as text (no HCL parser in the tree): a ``variable`` block's
``default`` and the ``module "stack"`` arguments are enough to find the value each environment
deploys. A cloud environment that sets either switch to anything but ``on`` fails here."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from account_intelligence.app.container import warn_if_uncapped
from account_intelligence.dev_limits import caps_lifted

ROOT = Path(__file__).resolve().parents[2]
STACK = ROOT / "infra" / "stack" / "main.tf"
CLOUD_ENVS = {
    "staging": ROOT / "infra" / "envs" / "staging",
    "prod": ROOT / "infra" / "envs" / "prod",
}
SWITCHES = {"research_caps": "RESEARCH_CAPS", "token_guard": "TOKEN_GUARD"}


def _variable_default(tf: str, name: str) -> str | None:
    """The string default of ``variable "<name>"`` in ``tf`` (None when not declared)."""
    block = re.search(rf'variable\s+"{name}"\s*\{{(.*?)\n\}}', tf, re.S)
    if block is None:
        return None
    default = re.search(r'^\s*default\s*=\s*"([^"]*)"', block.group(1), re.M)
    return default.group(1) if default else None


def _module_argument(tf: str, name: str) -> str | None:
    """The right-hand side of ``<name> = ...`` inside ``module "stack"`` (None when not passed)."""
    block = re.search(r'module\s+"stack"\s*\{(.*?)\n\}', tf, re.S)
    assert block is not None, 'every cloud environment instantiates module "stack"'
    arg = re.search(rf"^\s*{name}\s*=\s*(.+?)\s*$", block.group(1), re.M)
    return arg.group(1) if arg else None


def _effective(env_dir: Path, name: str) -> str:
    """The value the environment deploys for ``name`` when no tfvars override it."""
    env_tf = (env_dir / "main.tf").read_text(encoding="utf-8")
    stack_default = _variable_default(STACK.read_text(encoding="utf-8"), name)
    passed = _module_argument(env_tf, name)
    if passed is None:
        assert stack_default is not None, f"stack variable {name} needs a default"
        return stack_default
    literal = re.fullmatch(r'"([^"]*)"', passed)
    if literal:
        return literal.group(1)
    assert passed == f"var.{name}", f"{env_dir.name}: {name} = {passed} is not a literal or var"
    default = _variable_default(env_tf, name)
    assert default is not None, f"{env_dir.name}: variable {name} needs a default"
    return default


@pytest.mark.parametrize("env", sorted(CLOUD_ENVS))
def test_caps_default_on_in_cloud_profiles(env: str) -> None:
    for name in SWITCHES:
        assert _effective(CLOUD_ENVS[env], name) == "on", f"{env}: {name} must default to on"


@pytest.mark.parametrize("env", sorted(CLOUD_ENVS))
def test_tfvars_examples_do_not_switch_caps_off(env: str) -> None:
    example = CLOUD_ENVS[env] / "terraform.tfvars.example"
    text = example.read_text(encoding="utf-8") if example.exists() else ""
    for name in SWITCHES:
        assert not re.search(rf'^\s*{name}\s*=\s*"off"', text, re.M), f"{example}: {name} off"


def test_the_stack_passes_both_switches_to_the_services() -> None:
    stack = STACK.read_text(encoding="utf-8")
    for name, env_name in SWITCHES.items():
        assert re.search(rf"^\s*{env_name}\s*=\s*var\.{name}\s*$", stack, re.M), env_name
        assert _variable_default(stack, name) == "on"


def test_caps_lifted_names_every_cap_the_switch_turns_off() -> None:
    assert caps_lifted({"APP_ENV": "staging"}) == []
    assert caps_lifted({"APP_ENV": "staging", "RESEARCH_CAPS": "on"}) == []
    lifted = caps_lifted({"APP_ENV": "staging", "RESEARCH_CAPS": "off"})
    for cap in (
        "run_dollar_cap",
        "token_guard",
        "agent_budgets",
        "repeat_call_limit",
        "max_slices",
        "user_research_limit",
        "credit_pool",
    ):
        assert cap in lifted
    # TOKEN_GUARD=on keeps the token guard while the other caps are off
    kept = caps_lifted({"APP_ENV": "staging", "RESEARCH_CAPS": "off", "TOKEN_GUARD": "on"})
    assert "token_guard" not in kept and "run_dollar_cap" in kept


def test_uncapped_cloud_process_logs_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    for env in ("staging", "production"):
        caplog.clear()
        assert warn_if_uncapped({"APP_ENV": env, "RESEARCH_CAPS": "off"}) is True
        record = next(r for r in caplog.records if r.msg == "research.caps_off")
        assert record.levelno == logging.WARNING
        assert "run_dollar_cap" in record.caps_off  # type: ignore[attr-defined]


def test_dev_test_and_capped_processes_log_nothing(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    assert warn_if_uncapped({"APP_ENV": "dev", "RESEARCH_CAPS": "off"}) is False
    assert warn_if_uncapped({"APP_ENV": "test", "RESEARCH_CAPS": "off"}) is False
    assert warn_if_uncapped({"APP_ENV": "staging"}) is False
    assert not [r for r in caplog.records if r.msg == "research.caps_off"]
