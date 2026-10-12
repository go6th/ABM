"""Billing configuration, cost model, plan and cap-repository refusals.

A wrong ``spend.yaml`` must fail closed at load (never a silent default that moves money); a cost
line with inverted bounds is refused; a stage with no priced operation plans nothing; the cap
repository never records a decision twice and never moves a cap backwards or under the spend.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml
from pydantic import ValidationError

from account_intelligence.config import CONFIG_DIR
from account_intelligence.db.session import tenant_txn
from spend import limits, repo
from spend.config import SpendConfig, build_v2, load_v2_config
from spend.errors import ApprovalInvalid, ConfigError
from spend.models import OpCost

BASE: dict[str, Any] = yaml.safe_load((CONFIG_DIR / "spend.yaml").read_text())


def _with(key: str, value: Any) -> dict[str, Any]:
    data = copy.deepcopy(BASE)
    data["v2"][key]["value"] = value
    return data


def test_the_shipped_v2_section_loads() -> None:
    cfg = build_v2(BASE)
    assert 0 < cfg.soft_limit_pct < 100 and cfg.approver_roles


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("soft_limit_pct", 100, "below 100"),
        ("tenant_ceiling_usd", 25, "decimal string or null"),
        ("tenant_ceiling_usd", "not money", "v2:"),
        ("allow_alternate_on_exhaustion", "yes", "boolean"),
        ("approver_roles", [], "non-empty lists"),
        ("upload_content_types", "text/csv", "non-empty lists"),
        ("upload_max_age_days", 0, "positive integer or null"),
        ("upload_max_age_days", True, "positive integer or null"),
    ],
)
def test_a_wrong_v2_value_fails_closed(key: str, value: Any, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        build_v2(_with(key, value))


def test_an_unreadable_or_non_mapping_spend_file_is_refused(tmp_path: Path) -> None:
    load_v2_config.cache_clear()
    try:
        with pytest.raises(ConfigError, match="cannot load"):
            load_v2_config(tmp_path)  # no spend.yaml here
        (tmp_path / "spend.yaml").write_text("- a list, not a mapping\n")
        with pytest.raises(ConfigError, match="must contain a mapping"):
            load_v2_config(tmp_path)
        (tmp_path / "spend.yaml").write_text("v2: [unclosed\n")
        with pytest.raises(ConfigError, match="cannot load"):
            load_v2_config(tmp_path)  # malformed YAML
    finally:
        load_v2_config.cache_clear()


def test_cost_bounds_must_be_ordered() -> None:
    ok = OpCost(
        provider_code="p_search",
        operation="web_search",
        low_micros=1,
        expected_micros=2,
        high_micros=3,
        known=True,
        cost_source="documented",
    )
    assert ok.expected_micros == 2
    with pytest.raises(ValidationError, match="low <= expected <= high"):
        OpCost(
            provider_code="p_search",
            operation="web_search",
            low_micros=5,
            expected_micros=2,
            high_micros=3,
            known=True,
            cost_source="documented",
        )


def test_a_stage_with_no_priced_operation_plans_nothing() -> None:
    from account_intelligence.grading.config import load_grading_config
    from account_intelligence.workflow.spec import load_spec
    from spend.plan import spec_plan

    category = load_grading_config().category_by_operation
    assert spec_plan(load_spec(), {}, category, ()) == []  # no cost line: nothing to estimate


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_funding_mode_of_an_unreadable_run_is_stopped(tenant_a: UUID) -> None:
    async with tenant_txn(tenant_a) as s:
        assert await limits.funding_mode(s, uuid4()) is limits.Mode.STOPPED


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_cap_decision_is_recorded_once_and_a_cap_never_moves_back(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    from tests.spend.conftest import admin
    from tests.spend.test_connector_gate import _approved

    _, run, _ = await _approved(tenant_a, cfg)
    version = admin("SELECT cap_version FROM account_runs WHERE id = %s", run)[0][0]
    with pytest.raises(ApprovalInvalid, match="already decided"):
        async with tenant_txn(tenant_a) as s:
            await repo.add_event(s, run, version, "approved", "human:u")
    before = admin("SELECT budget_usd, cap_version FROM account_runs WHERE id = %s", run)
    with pytest.raises(ApprovalInvalid, match="cannot go back"):
        async with tenant_txn(tenant_a) as s:
            await repo.apply_cap(s, run, version, 50_000_000, {})  # same version: not forward
    admin("UPDATE account_runs SET spent_usd = 1.0 WHERE id = %s", run)
    with pytest.raises(ApprovalInvalid, match="below the spend"):
        async with tenant_txn(tenant_a) as s:
            await repo.apply_cap(s, run, version + 1, 500_000, {})  # $0.50 cap under $1 spent
    assert admin("SELECT budget_usd, cap_version FROM account_runs WHERE id = %s", run) == before
