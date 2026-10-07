"""WS9 hardening (HD1 to HD3, RF8). Lying scenarios: a worker killed between claim and run, a worker
killed after the run before finishing the slot, a database failure mid-probe, a status page with
an unknown status or a provider name."""

from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.ports import NotConfigured
from refresh import probe, records, slots
from signals.registry import KINDS
from tests.fakes.grader import FakeGrader
from tests.fakes.phase3 import FakeClock
from tests.fakes.world import EPOCH, FakeMonitor, Happening, WorldScript
from tests.phase4.conftest import db, tenant_with_account
from tests.phase4.test_ws10_simulation import CFG, RULES

ROOT = Path(__file__).resolve().parents[2]


def _status() -> object:
    spec = importlib.util.spec_from_file_location("status_gen", ROOT / "ops/status/generate.py")
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_status_page_is_generated_escaped_and_client_safe() -> None:
    gen = _status()
    page = gen.render(
        {
            "generated_at": "2026-03-02T09:00Z",  # type: ignore[attr-defined]
            "components": [{"name": "Alerts <b>", "status": "degraded"}],
            "incidents": [
                {"title": "Slow digests", "status": "monitoring", "started_at": "2026-03-02T08:00Z"}
            ],
            "maintenance": [],
        }
    )
    assert "Alerts &lt;b&gt;" in page and "degraded" in page and "<li>None</li>" in page
    with pytest.raises(ValueError):
        gen.render({"generated_at": "x", "components": [{"name": "a", "status": "fine"}]})  # type: ignore[attr-defined]
    with pytest.raises(NotConfigured):
        gen.publish(page)  # type: ignore[attr-defined]
    from account_intelligence.connectors.registry_internal import vendor_names
    from account_intelligence.connectors.settings import load_providers_config
    from account_intelligence.safety.client_output import ClientOutputLeak

    vendor = sorted(n for n in vendor_names(load_providers_config()).values() if n != "Parallel")[0]
    with pytest.raises(ClientOutputLeak):
        gen.render(
            {
                "generated_at": "x",
                "components": [  # type: ignore[attr-defined]
                    {"name": f"{vendor} feed", "status": "operational"}
                ],
            }
        )


class Boom(RuntimeError):
    """A simulated crash."""


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_killed_workers_recover_without_duplicate_runs_or_alerts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t, a = tenant_with_account("p4-kill", "kill.example")
    w = WorldScript(
        happenings=[
            Happening(
                "kill.example",
                "build_role_postings",
                "p",
                "build_role_posting",
                "Kill",
                "Platform owner",
                0,
                None,
                ("https://j.example",),
                "t1",
            )
        ]
    )
    clock = FakeClock()
    clock.current = EPOCH
    deps = probe.Deps(
        {k: FakeMonitor(k, w, clock) for k in KINDS},
        FakeGrader(),
        ["u"],
        "https://app.test",
        "pilot",
        1,
        CFG["delta_weights"],
        RULES,
        2,
        0.8,
        0.5,
        7,
    )

    async def claim_one() -> slots.Slot | None:
        async with tenant_txn(t) as s:
            got = await slots.claim(s, now=clock.current, worker="w", limit=1)
        return got[0] if got else None

    async def run(slot: slots.Slot) -> probe.ProbeResult:
        return await probe.run_probe(
            tenant_txn,
            t,
            account_id=a,
            domain="kill.example",
            kinds=KINDS,
            brief_built_at=clock.current,
            priority_a=False,
            account_score=1,
            slot_id=slot.id,
            deps=deps,
            now=clock.current,
        )

    async with tenant_txn(t) as s:
        await slots.ensure(s, account_id=a, kind="probe", period="2026-W02", due_at=EPOCH)
    # 1. killed after the claim, before the run: the lease releases it and it runs once
    first = await claim_one()
    assert first is not None and await claim_one() is None
    clock.current += timedelta(minutes=30)
    async with tenant_txn(t) as s:
        assert await slots.recover(s, now=clock.current, lease=timedelta(minutes=15)) == {
            "recovered": 0,
            "released": 1,
        }
    again = await claim_one()
    assert again is not None and again.id == first.id
    # 2. a database failure inside the final transaction: nothing of the probe is kept
    orig = records.record_delta

    async def failing(*args: object, **kw: object) -> object:
        raise Boom("connection lost")

    monkeypatch.setattr(records, "record_delta", failing)
    with pytest.raises(Boom):
        await run(again)
    monkeypatch.setattr(records, "record_delta", orig)
    assert db("SELECT count(*) FROM refresh_runs WHERE tenant_id = %s", t) == [(0,)]
    assert db("SELECT count(*) FROM alerts WHERE tenant_id = %s", t) == [(0,)]
    clock.current += timedelta(minutes=30)
    async with tenant_txn(t) as s:
        assert (await slots.recover(s, now=clock.current, lease=timedelta(minutes=15)))[
            "released"
        ] == 1
    third = await claim_one()
    assert third is not None
    # 3. killed after the run, before finishing the slot: recovery marks it done, no second run
    res = await run(third)
    assert len(res.alerts) == 1
    clock.current += timedelta(minutes=30)
    async with tenant_txn(t) as s:
        assert await slots.recover(s, now=clock.current, lease=timedelta(minutes=15)) == {
            "recovered": 1,
            "released": 0,
        }
    assert await claim_one() is None
    assert db("SELECT count(*) FROM refresh_runs WHERE tenant_id = %s", t) == [(1,)]
    assert db("SELECT count(*) FROM alerts WHERE tenant_id = %s", t) == [(1,)]
    assert db("SELECT status, outcome FROM refresh_slots WHERE tenant_id = %s", t) == [
        ("done", "recovered")
    ]
