"""Run modes (D-102, G14 to G17), end to end on the stub world."""

from __future__ import annotations

import pytest

from agents.errors import InvalidMode, NoPulledData
from tests.agents.conftest import Harness
from tests.agents.fakes import Killed

pytestmark = pytest.mark.asyncio


def snapshot(h: Harness, run_id):  # type: ignore[no-untyped-def]
    """Run-independent outputs: facts, tracker rows and built files."""
    facts = [
        (f.fact_id, f.row_key, f.attribute, f.value, f.grade, f.visibility)
        for f in sorted(h.grading.facts_by_run[run_id].values(), key=lambda f: f.fact_id)
    ]
    rows = sorted(h.tracker.rows_by_run[run_id].items())
    files = sorted(fid for rid, fids in h.delivery.released if rid == run_id for fid in fids)
    return facts, rows, files, [h.builder.files[f] for f in files]


# G14 -------------------------------------------------------------------------------------------


async def test_default_run_does_both_phases(h: Harness) -> None:
    h.grant_consent()
    record, state = await h.run("k1")
    assert record.mode == "full"
    assert state["outcome"] in ("completed", "partial") and state.get("error_code") is None
    assert h.manifests.by_run[record.run_id]
    assert h.grading.facts_by_run[record.run_id]
    assert h.delivery.released and h.wallet.settlements[record.run_id] == 1


async def test_pull_mode_produces_a_manifest_and_nothing_else(h: Harness) -> None:
    record, state = await h.run("p1", mode="pull")
    manifest = h.manifests.by_run[record.run_id]
    assert state["outcome"] == "completed" and state["scope"] == "pull"
    assert {r.row_key for r in manifest.rows} == set(h.cfg.plan)
    assert all(r.queries or r.status == "blocked" for r in manifest.rows)
    assert h.grading.graded_items == 0 and not h.grading.facts_by_run
    assert not h.tracker.reports and not h.builder.builds and not h.delivery.released
    assert not h.records.sense and h.llm is not None and not h.llm.calls
    assert h.wallet.settlements[record.run_id] == 1
    assert h.credit.spent[record.run_id] == manifest.total_cost_micros() > 0


# G16 -------------------------------------------------------------------------------------------


async def test_build_without_pull_fails_with_no_pulled_data(h: Harness) -> None:
    with pytest.raises(NoPulledData) as error:
        await h.start("b0", mode="build")
    assert error.value.code == "no_pulled_data" and set(error.value.missing) == set(h.cfg.plan)
    assert h.provider.new_calls == 0


async def test_build_never_runs_phase_one(h: Harness) -> None:
    h.grant_consent()
    pull, _ = await h.run("p", mode="pull")
    calls_after_pull = h.provider.new_calls
    build, state = await h.run("b", mode="build", parent_run_id=pull.run_id)
    assert build.parent_run_id == pull.run_id and build.mode == "build"
    assert state.get("error_code") is None
    assert h.provider.new_calls == calls_after_pull  # no Phase 1 call in the build
    assert h.wallet.settlements[pull.run_id] == 1 and h.wallet.settlements[build.run_id] == 1


async def test_build_with_a_foreign_or_missing_parent_fails_fast(make) -> None:  # type: ignore[no-untyped-def]
    a, b = make(), make()
    pull, _ = await a.run("p", mode="pull")
    with pytest.raises(NoPulledData):
        await b.start("b", mode="build", parent_run_id=pull.run_id)  # other tenant


async def test_build_with_an_incomplete_manifest_lists_missing_rows(h: Harness) -> None:
    pull, _ = await h.run("p", mode="pull")
    m = h.manifests.by_run[pull.run_id]
    h.manifests.by_run[pull.run_id] = m.model_copy(update={"rows": m.rows[:-2]})
    _, state = await h.run("b", mode="build", parent_run_id=pull.run_id)
    assert state["outcome"] == "failed" and state["error_code"] == "no_pulled_data"
    assert set(state["missing_rows"]) == {r.row_key for r in m.rows[-2:]}
    assert not h.builder.builds and not h.delivery.released


async def test_stale_pull_requires_acknowledgement(h: Harness) -> None:
    h.grant_consent()
    pull, _ = await h.run("p", mode="pull")
    h.clock.advance(days=8)  # news and leadership have 7-day shelf lives
    _, state = await h.run("b1", mode="build", parent_run_id=pull.run_id)
    assert state["error_code"] == "stale_pulls" and "company_news" in state["missing_rows"]
    ack = list(state["missing_rows"])
    _, state = await h.run(
        "b2", mode="build", parent_run_id=pull.run_id, acknowledged_stale_rows=ack
    )
    assert state.get("error_code") is None and state["counters"]["stale_acknowledged"] == len(ack)


async def test_build_with_new_pull_proposal_pauses_for_approval(make) -> None:  # type: ignore[no-untyped-def]
    from tests.agents import worlds

    h = make(worlds.tangled())
    pull, _ = await h.run("p", mode="pull")
    record = await h.start("b", mode="build", parent_run_id=pull.run_id, attended=True, budget=0)
    state = await h.service.execute(h.tenant, record.run_id)
    pending = await h.service.pending(h.tenant, record.run_id)
    assert pending and pending[0]["reason"] == "sense_pulls"
    assert h.provider.new_calls and not h.builder.builds
    h.credit.approve(record.run_id, 1_000_000)
    state = await h.service.resume(h.tenant, record.run_id, True)
    assert state["counters"]["targeted_pulls"] == 1 and h.builder.builds


# G17 -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("world_name", ["rich", "sparse", "tangled"])
async def test_full_equals_pull_then_build(make, world_name: str) -> None:  # type: ignore[no-untyped-def]
    from tests.agents import worlds

    one, two = make(getattr(worlds, world_name)()), make(getattr(worlds, world_name)())
    one.grant_consent()
    two.grant_consent()
    full, s1 = await one.run("f")
    pull, _ = await two.run("p", mode="pull")
    build, s2 = await two.run("b", mode="build", parent_run_id=pull.run_id)
    assert s1["outcome"] == s2["outcome"]
    left, right = snapshot(one, full.run_id), snapshot(two, build.run_id)
    assert left == right
    facts, rows, files, contents = left
    assert facts and len(rows) == len(one.cfg.plan) and files and all(contents)


async def test_build_on_an_older_config_needs_an_explicit_choice(h: Harness) -> None:
    from agents.errors import GuardViolation

    pull, _ = await h.run("p", mode="pull")
    h.runs.by_id[pull.run_id] = h.runs.by_id[pull.run_id].__class__(
        **{**h.runs.by_id[pull.run_id].__dict__, "config_version": "sk-older"}
    )
    with pytest.raises(GuardViolation) as error:
        await h.start("b", mode="build", parent_run_id=pull.run_id)
    assert error.value.code == "config_version_unavailable"
    record = await h.start(
        "b2", mode="build", parent_run_id=pull.run_id, config_version=h.cfg.config_version
    )
    assert record.config_version_reason == "explicit_override"


# Idempotency and durability --------------------------------------------------------------------


async def test_duplicate_start_is_idempotent(h: Harness) -> None:
    a = await h.start("same")
    b = await h.start("same")
    assert a == b and len(h.runs.by_id) == 1
    with pytest.raises(InvalidMode):
        await h.start("same", mode="pull")


async def test_execute_twice_settles_once_and_spends_once(h: Harness) -> None:
    record, _ = await h.run("x", mode="pull")
    spent = h.credit.spent[record.run_id]
    await h.service.execute(h.tenant, record.run_id)
    assert h.wallet.settlements[record.run_id] == 1 and h.credit.spent[record.run_id] == spent


async def test_kill_mid_pull_and_resume_spends_once(make) -> None:  # type: ignore[no-untyped-def]
    clean, crashy = make(), make()
    ref, _ = await clean.run("p", mode="pull")
    record = await crashy.start("p", mode="pull")
    crashy.provider.crash_after = 20
    with pytest.raises(Killed):
        await crashy.service.execute(crashy.tenant, record.run_id)
    crashy.rebuild()
    state = await crashy.service.execute(crashy.tenant, record.run_id)
    assert state["outcome"] == "completed"
    assert crashy.credit.spent[record.run_id] == clean.credit.spent[ref.run_id]
    assert crashy.provider.paid_calls == clean.provider.paid_calls
    assert crashy.wallet.settlements[record.run_id] == 1


async def test_build_option_shows_date_and_coverage(h: Harness) -> None:
    assert await h.service.build_option(h.tenant, h.world.domain) is None
    pull, _ = await h.run("p", mode="pull")
    option = await h.service.build_option(h.tenant, h.world.domain.upper())
    assert option is not None and option.pull_run_id == pull.run_id
    assert option.rows_stored + option.rows_blocked == len(h.cfg.plan)
    assert option.pulled_at == h.clock.now()
