"""Whole-run guard tests on the stub world: G6, G7, G8, G9, G10, D-98, gate, logging."""

from __future__ import annotations

import logging

import pytest

from agents.ports import InternalHistorySettings, TenantAgentSettings
from tests.agents import worlds
from tests.agents.conftest import Harness
from tests.agents.fakes import PRICES, FakeInternal

pytestmark = pytest.mark.asyncio
PAID = {k for k, v in PRICES.items() if v}


def manifest(h: Harness, run_id):  # type: ignore[no-untyped-def]
    return {r.row_key: r for r in h.manifests.by_run[run_id].rows}


# G6 --------------------------------------------------------------------------------------------


async def test_sense_check_always_precedes_build(h: Harness) -> None:
    events: list[str] = []
    save, build = h.records.save_sense_check, h.builder.build

    async def saving(ctx, record):  # type: ignore[no-untyped-def]
        events.append("sense")
        await save(ctx, record)

    async def building(ctx, audience):  # type: ignore[no-untyped-def]
        events.append("build")
        return await build(ctx, audience)

    h.records.save_sense_check = saving  # type: ignore[method-assign]
    h.builder.build = building  # type: ignore[method-assign]
    await h.run("r")
    assert events and events[0] == "sense" and "build" in events
    assert events.index("sense") < events.index("build")


async def test_g6_build_attempt_without_a_sense_check_is_refused(h: Harness) -> None:
    async def lose(ctx, record):  # type: ignore[no-untyped-def]
        return None

    h.records.save_sense_check = lose  # type: ignore[method-assign]
    _, state = await h.run("r")
    assert (state["outcome"], state["error_code"]) == ("failed", "sense_check_missing")
    assert not h.builder.builds and not h.delivery.released


async def test_g6_facts_changed_after_the_sense_check_are_refused(h: Harness) -> None:
    facts = h.grading.facts

    async def changed(ctx):  # type: ignore[no-untyped-def]
        current = await facts(ctx)
        if h.records.sense.get(ctx.run_id):
            return current[1:]
        return current

    h.grading.facts = changed  # type: ignore[method-assign]
    _, state = await h.run("r")
    assert state["error_code"] == "sense_check_stale" and not h.builder.builds


# G7 --------------------------------------------------------------------------------------------


async def test_g7_unapproved_pull_in_an_attended_run_pauses(h: Harness) -> None:
    record = await h.start("r", attended=True, budget=None)
    await h.service.execute(h.tenant, record.run_id)
    assert (await h.service.pending(h.tenant, record.run_id))[0]["awaiting"] == "approval"
    assert h.provider.new_calls == 0
    h.credit.approve(record.run_id, 10_000_000)
    state = await h.service.resume(h.tenant, record.run_id, True)
    assert state["outcome"] in ("completed", "partial") and h.provider.paid_calls


async def test_g7_cap_reached_mid_pull_pauses_then_resumes_once(h: Harness) -> None:
    record = await h.start("r", attended=True, budget=60_000)
    await h.service.execute(h.tenant, record.run_id)
    pending = await h.service.pending(h.tenant, record.run_id)
    assert pending[0]["reason"] == "spend" and pending[0]["row"]
    spent_at_pause = h.credit.spent[record.run_id]
    h.credit.approve(record.run_id, 10_000_000)
    state = await h.service.resume(h.tenant, record.run_id, True)
    assert state.get("error_code") is None
    assert h.credit.spent[record.run_id] > spent_at_pause
    rows = manifest(h, record.run_id)
    assert all(
        r.status == "stored"
        for r in rows.values()
        if r.row_key in h.cfg.plan and not h.cfg.plan[r.row_key].people
    )


async def test_g7_declined_spend_blocks_paid_rows_and_keeps_free_ones(h: Harness) -> None:
    record = await h.start("r", attended=True, budget=0)
    await h.service.execute(h.tenant, record.run_id)
    state = await h.service.resume(h.tenant, record.run_id, False)
    rows = manifest(h, record.run_id)
    paid = [r for k, r in rows.items() if h.cfg.plan[k].need in PAID and not h.cfg.plan[k].people]
    free = [
        r for k, r in rows.items() if h.cfg.plan[k].need not in PAID and not h.cfg.plan[k].people
    ]
    assert paid and all(r.blocked_reason == "spend_not_approved" for r in paid)
    assert free and all(r.status == "stored" for r in free)
    assert h.credit.spent[record.run_id] == 0 and state["outcome"] == "partial"


async def test_g7_unattended_run_records_instead_of_pausing(h: Harness) -> None:
    record, state = await h.run("r", budget=None)
    assert not await h.service.pending(h.tenant, record.run_id)
    rows = manifest(h, record.run_id)
    assert any(r.blocked_reason == "spend_not_approved" for r in rows.values())
    assert h.credit.spent[record.run_id] == 0 and state["outcome"] == "partial"


async def test_unattended_sense_proposals_are_recorded_and_rows_stay_partial(make) -> None:  # type: ignore[no-untyped-def]
    h = make(worlds.tangled())
    record, state = await h.run("r", budget=0)
    row = h.tracker.rows_by_run[record.run_id]["leadership_changes"]
    assert (row.status, row.reason) in {
        ("partial", "pull_not_approved"),
        ("blocked", "spend_not_approved"),
    }
    assert not h.credit.requests and state.get("counters", {}).get("targeted_pulls", 0) == 0


async def test_declined_sense_pulls_leave_rows_partial(make) -> None:  # type: ignore[no-untyped-def]
    h = make(worlds.tangled())
    pull, _ = await h.run("p", mode="pull")
    record = await h.start("b", mode="build", parent_run_id=pull.run_id, attended=True, budget=0)
    await h.service.execute(h.tenant, record.run_id)
    state = await h.service.resume(h.tenant, record.run_id, False)
    row = h.tracker.rows_by_run[record.run_id]["leadership_changes"]
    assert (row.status, row.reason) == ("partial", "pull_not_approved")
    assert h.credit.spent[record.run_id] == 0 and state["outcome"] == "partial"


# G8 --------------------------------------------------------------------------------------------


async def test_g8_people_rows_blocked_without_consent(h: Harness) -> None:
    record, _ = await h.run("r")
    people = [k for k, r in h.cfg.plan.items() if r.people]
    rows = manifest(h, record.run_id)
    assert all(rows[k].blocked_reason == "consent_required" for k in people)
    assert not any(
        need in ("people_by_technology", "profile_enrichment") for (_, need, _) in h.provider.calls
    )
    assert h.llm is not None and h.llm.calls["abm_people_extract"] == 0
    assert all(h.tracker.rows_by_run[record.run_id][k].reason == "consent_required" for k in people)


async def test_g8_people_pull_runs_with_consent(h: Harness) -> None:
    h.grant_consent()
    record, _ = await h.run("r")
    assert manifest(h, record.run_id)["people_by_platform"].status == "stored"
    assert {p.name for p in h.grading.people[record.run_id]} == {
        "Avery Quinn",
        "Rowan Hale",
        "Sam Ortiz",
    }


async def test_g8_without_approved_consent_text_no_record_is_valid(make) -> None:  # type: ignore[no-untyped-def]
    from agents.config import load_skill_config

    h = make(cfg=load_skill_config())  # the real config: consent text is TODO (D-97)
    h.grant_consent("anything")
    record, _ = await h.run("r")
    assert manifest(h, record.run_id)["people_by_platform"].blocked_reason == "consent_required"


# G9 --------------------------------------------------------------------------------------------


def _vendors() -> list[str]:
    """Paid provider display names from config (vendor names live only in allowed places)."""
    import yaml

    from account_intelligence.config import CONFIG_DIR

    brokers = yaml.safe_load((CONFIG_DIR / "routing.yaml").read_text())["brokers"]
    return sorted(str(b["display_name"]) for b in brokers.values() if b["paid"])


@pytest.mark.parametrize(
    "planted",
    [f"Sourced via {name}" for name in _vendors()] + ["Used 12 credits", "p_people result"],
)
async def test_g9_planted_tool_names_are_caught_in_every_deliverable(h: Harness, planted) -> None:  # type: ignore[no-untyped-def]
    h.builder.plant = planted
    _, state = await h.run("r")
    assert (state["outcome"], state["error_code"]) == ("failed", "client_output_leak")
    assert not h.delivery.released


async def test_g9_person_name_digest_is_caught(h: Harness, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import agents.naming as naming

    stand_in = "Zorblax"  # the real name is held only as a digest
    monkeypatch.setattr(naming, "name_digests", lambda: frozenset({naming.word_digest(stand_in)}))
    h.builder.plant = f"{stand_in.upper()} Only notes"
    _, state = await h.run("r")
    assert state["error_code"] == "client_output_leak" and not h.delivery.released


# G10 / D-98 ------------------------------------------------------------------------------------


def internal_on() -> TenantAgentSettings:
    return TenantAgentSettings(
        pursuing_client="Synthetic Partner Co",
        lanes=("salesforce",),
        internal_history=InternalHistorySettings(True, True, ("Account notes",), True),
    )


async def test_internal_history_is_off_by_default(make) -> None:  # type: ignore[no-untyped-def]
    h = make(internal=FakeInternal("prior: The client met Northwind in March 2026."))
    await h.run("r")
    assert h.internal is not None and h.internal.searches == 0


async def test_g10_private_facts_render_only_in_internal_only(make) -> None:  # type: ignore[no-untyped-def]
    h = make(internal=FakeInternal("prior: The client met Northwind in March 2026 about MuleSoft."))
    h.settings.settings = internal_on()
    record, _ = await h.run("r")
    assert h.internal is not None and h.internal.searches == 2
    private = [
        f for f in h.grading.facts_by_run[record.run_id].values() if f.visibility == "private"
    ]
    assert private
    client = [f for f in h.builder.files if f.startswith("client-")]
    internal = [f for f in h.builder.files if f.startswith("internal-")]
    value = private[0].value
    assert all(value not in line for f in client for line in h.builder.files[f])
    assert any(value in line for f in internal for line in h.builder.files[f])
    released = [fid for _, fids in h.delivery.released for fid in fids]
    assert released and all(f.startswith("client-") for f in released)


# Gate, logging ---------------------------------------------------------------------------------


async def test_gate_failure_stops_delivery(h: Harness) -> None:
    h.gate.failing = {"broken_links": ["row x"]}
    _, state = await h.run("r")
    assert (state["outcome"], state["error_code"]) == ("partial", "gate_failed")
    assert not h.delivery.released


async def test_setup_with_an_unknown_lane_fails_fast(h: Harness) -> None:
    h.settings.settings = TenantAgentSettings(pursuing_client="Partner", lanes=("cobol",))
    record, state = await h.run("r")
    assert (state["outcome"], state["error_code"]) == ("failed", "invalid_setup")
    assert h.provider.new_calls == 0 and h.wallet.settlements[record.run_id] == 1


async def test_a_run_without_a_pursuing_client_researches_the_account_by_name(h: Harness) -> None:
    h.settings.settings = TenantAgentSettings(pursuing_client=None, lanes=())
    _, state = await h.run("r")
    assert state.get("error_code") != "invalid_setup"
    assert h.provider.new_calls > 0


async def test_prompts_and_documents_are_never_logged(make, caplog) -> None:  # type: ignore[no-untyped-def]
    world = worlds.rich()
    world.pages["company_news"] = [
        world.pages["company_news"][0].__class__(
            "https://northwind-synthetic.example/n", "launch: CANARY-doc-91c2 Northwind body text."
        )
    ]
    h = make(world)
    caplog.set_level(logging.DEBUG)
    await h.run("r")
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "CANARY-doc-91c2" not in logged
    assert h.llm is not None and not any(p[:60] in logged for p in h.llm.prompts)
