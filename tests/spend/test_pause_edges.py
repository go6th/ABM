"""Pause-instead-of-fail edges on real Postgres (credit manager v2, D-45): a ceiling that leaves no
room, an approval below what was already spent, a request of an older cap version, a funded
wallet extension, and races between deciders.

Every refusal must leave the cap unchanged; every race must leave exactly one decision.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from account_intelligence.db.session import tenant_txn
from spend import limits, pause
from spend.config import SpendConfig
from spend.errors import ApprovalInvalid
from tests.spend.conftest import NOW, admin
from tests.spend.test_connector_gate import _approved
from tests.spend.test_v2_db import V2, approver, cap_of, open_request

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def decisions(request_id: UUID) -> list[tuple[str, str]]:
    return [
        (str(r[0]), str(r[1]))
        for r in admin(
            "SELECT event, principal FROM spend_limit_events WHERE request_id = %s"
            " AND event IN ('approved', 'declined', 'timed_out') ORDER BY id",
            request_id,
        )
    ]


async def test_a_ceiling_with_no_room_declines_the_pause_at_once(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    """When the tenant ceiling is the current cap, no larger cap can be suggested: the request
    is opened and declined by policy (``ceiling_reached``), and nobody is asked to approve."""
    _, run, _ = await _approved(tenant_a, cfg)
    async with tenant_txn(tenant_a) as s:
        inputs = await limits.read_inputs(s, run)
        assert inputs is not None and inputs.cap_micros is not None
        req = pause.compute_request(
            inputs.cap_version or 1,
            inputs.cap_micros,
            inputs.committed_micros,
            inputs.held_micros,
            [pause.BlockedUnit("body|jobs", 300_000, ("job_board_by_lane",))],
            cfg.rounding_step_micros,
            cfg.sample_hold_micros,
            inputs.cap_micros,  # ceiling == current cap
        )
        assert req.capped_by_ceiling and not req.room
        request_id, opened = await pause.open_pause(s, run, req, NOW, V2)
    assert not opened
    assert decisions(request_id) == [("declined", "policy")]
    detail = admin(
        "SELECT detail_json FROM spend_limit_events WHERE request_id = %s AND event = 'declined'",
        request_id,
    )[0][0]
    assert detail == {"reason": "ceiling_reached"}
    assert admin("SELECT count(*) FROM notification_outbox WHERE run_id = %s", run) == [(0,)]


async def test_an_approval_below_the_spend_already_made_is_refused(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    """An overshoot can put the spend above the cap; a new cap must still cover it."""
    _, run, _ = await _approved(tenant_a, cfg)
    request_id, _ = await open_request(tenant_a, run, cfg)
    cap_usd, _ = cap_of(run)
    admin(
        "UPDATE account_runs SET cap_overshoot_usd = 1, spent_usd = %s WHERE id = %s",
        Decimal(cap_usd) + Decimal("0.50"),
        run,
    )
    amount = int((Decimal(cap_usd) + Decimal("0.25")) * 1_000_000)  # above the cap, below spend
    before = cap_of(run)
    with pytest.raises(ApprovalInvalid, match="below the spend"):
        async with tenant_txn(tenant_a) as s:
            await pause.approve(
                s,
                run,
                request_id,
                approver(tenant_a),
                amount,
                NOW,
                V2,
                cfg,
                pause.UnconfiguredWallet(),
            )
    assert cap_of(run) == before and decisions(request_id) == []


async def test_a_request_of_an_older_cap_version_cannot_be_decided(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    _, run, _ = await _approved(tenant_a, cfg)
    request_id, _ = await open_request(tenant_a, run, cfg)
    admin("UPDATE account_runs SET cap_version = cap_version + 1 WHERE id = %s", run)
    with pytest.raises(ApprovalInvalid, match="older cap version"):
        async with tenant_txn(tenant_a) as s:
            await pause.decline(s, run, request_id, approver(tenant_a), NOW, V2)
    assert decisions(request_id) == []


async def test_a_funded_extension_reserves_whole_credits_from_the_wallet(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    """With a credit rate the approval reserves the extra credits (the cheapest rate, rounded),
    and records how many in the decision."""
    rated = dataclasses.replace(cfg, credit_usd_rates={"p_people": Decimal("0.01")})
    asked: list[Decimal] = []

    class Paying:
        async def reserve_extra(self, tenant_id: UUID, run_id: UUID, credits: Decimal) -> bool:
            asked.append(credits)
            return True

    _, run, _ = await _approved(tenant_a, cfg)
    request_id, _ = await open_request(tenant_a, run, cfg)
    old_cap, _ = cap_of(run)
    async with tenant_txn(tenant_a) as s:
        new = await pause.approve(
            s, run, request_id, approver(tenant_a), None, NOW, V2, rated, Paying()
        )
    extra = (Decimal(new) - Decimal(old_cap) * 1_000_000) / Decimal(1_000_000) / Decimal("0.01")
    assert asked == [extra.quantize(Decimal("1"))]
    detail = admin(
        "SELECT detail_json FROM spend_limit_events WHERE request_id = %s AND event = 'approved'",
        request_id,
    )[0][0]
    assert detail["wallet"] == f"reserved {asked[0]} credits"


async def test_two_deciders_at_once_leave_exactly_one_decision(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    """A decline and a timeout racing on one request: one wins, the loser changes nothing
    (the unique decision index refuses it and the timeout reports that it did not decide)."""
    _, run, _ = await _approved(tenant_a, cfg)
    request_id, _ = await open_request(tenant_a, run, cfg)
    later = NOW + timedelta(hours=V2.pause_timeout_hours + 1)
    held = asyncio.Event()
    release = asyncio.Event()

    async def decline_and_hold() -> None:
        async with tenant_txn(tenant_a) as s:
            await pause.decline(s, run, request_id, approver(tenant_a), NOW, V2)
            held.set()
            await release.wait()

    async def expire_late() -> UUID | None:
        await held.wait()
        async with tenant_txn(tenant_a) as s:
            return await pause.expire(s, run, later, V2)

    first = asyncio.create_task(decline_and_hold())
    second = asyncio.create_task(expire_late())
    await asyncio.wait_for(held.wait(), 10)
    await asyncio.sleep(0.3)  # the expiry is now blocked on the decision's unique index
    release.set()
    await asyncio.wait_for(first, 10)
    assert await asyncio.wait_for(second, 10) is None
    assert decisions(request_id) == [("declined", "human:u1")]


async def test_a_racing_opener_returns_the_winner_among_older_versions(
    tenant_a: UUID, cfg: SpendConfig
) -> None:
    """Two openers of the next cap version while an older version's request exists: the loser
    finds the winner's request (skipping the older one) instead of opening a second."""
    _, run, _ = await _approved(tenant_a, cfg)
    first_id, _ = await open_request(tenant_a, run, cfg)
    async with tenant_txn(tenant_a) as s:
        await pause.approve(
            s, run, first_id, approver(tenant_a), None, NOW, V2, cfg, pause.UnconfiguredWallet()
        )
    held = asyncio.Event()
    release = asyncio.Event()

    async def open_and_hold() -> tuple[UUID, bool]:
        async with tenant_txn(tenant_a) as s:
            out = await _open_in(s, run, cfg)
            held.set()
            await release.wait()
            return out

    async def open_second() -> tuple[UUID, bool]:
        await held.wait()
        async with tenant_txn(tenant_a) as s:
            return await _open_in(s, run, cfg)

    a = asyncio.create_task(open_and_hold())
    b = asyncio.create_task(open_second())
    await asyncio.wait_for(held.wait(), 10)
    await asyncio.sleep(0.3)
    release.set()
    won, lost = await asyncio.wait_for(a, 10), await asyncio.wait_for(b, 10)
    assert won == lost and won[0] != first_id
    opened = admin(
        "SELECT count(*) FROM spend_limit_events WHERE run_id = %s AND event = 'pause_opened'", run
    )
    assert opened == [(2,)]  # one per cap version


async def _open_in(s: object, run: UUID, cfg: SpendConfig) -> tuple[UUID, bool]:
    inputs = await limits.read_inputs(s, run)  # type: ignore[arg-type]
    assert inputs is not None and inputs.cap_micros is not None
    req = pause.compute_request(
        inputs.cap_version or 1,
        inputs.cap_micros,
        inputs.committed_micros,
        inputs.held_micros,
        [pause.BlockedUnit("body|jobs", 300_000, ("job_board_by_lane",))],
        cfg.rounding_step_micros,
        cfg.sample_hold_micros,
        None,
    )
    return await pause.open_pause(s, run, req, NOW, V2)  # type: ignore[arg-type]
