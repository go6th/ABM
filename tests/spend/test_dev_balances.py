"""The local-stack spend opt-ins: an assumed, labelled ``ok`` balance, dev only."""

from __future__ import annotations

import pytest

from spend.balances import DevAssumedBalances


@pytest.mark.asyncio
async def test_dev_assumed_balance_is_ok_and_labelled() -> None:
    snap = await DevAssumedBalances().balance("p_search")
    assert snap.status == "ok"
    assert snap.unit == "dev_assumed"
    assert "dev assumption" in snap.source


@pytest.mark.parametrize(("app_env", "on"), [("dev", True), ("test", False)])
def test_dev_switches_apply_to_the_local_stack_only(app_env: str, on: bool) -> None:
    from account_intelligence.app.container import build_container

    env = {
        "APP_ENV": app_env,
        "DEV_ASSUME_PROVIDER_BALANCE": "1",
        "DEV_AUTO_APPROVE_CAPS": "1",
    }
    container = build_container(env)
    assert isinstance(container.balances, DevAssumedBalances) is on
    assert container.dev_auto_approve is on
