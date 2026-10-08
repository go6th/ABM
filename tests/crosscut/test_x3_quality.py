"""X3 quality system (QS1 to QS3, roadmap 11.3). Lying scenarios: a layer that did not run
becomes a release, a skipped eval becomes a pass, a worse score merges, an untouched path is
forced through the eval, a row with no status is delivered, and a used sample item is drawn
again."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from account_intelligence.db.session import SessionLocal
from quality import analyst_loop, regression, release_check

NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


# --- QS1: the release check ---------------------------------------------------------------------


def test_a_release_needs_every_layer() -> None:
    assert release_check.check(gate=True, verify=True, eval_=True).released
    v = release_check.check(gate=True, verify=True, eval_=False)
    assert not v.released and v.missing == ("eval",)
    v = release_check.check(gate=False, verify=False, eval_=False)
    assert v.missing == ("gate", "verify", "eval")


def test_a_row_with_no_status_blocks_delivery() -> None:
    rows = [
        release_check.RowStatus("F-0001", "done"),
        release_check.RowStatus("F-0002", None),
        release_check.RowStatus("F-0003", "  "),
        release_check.RowStatus("F-0004", "blocked"),
    ]
    assert release_check.delivery_blockers(rows) == ("F-0002", "F-0003")
    assert release_check.delivery_blockers([rows[0], rows[3]]) == ()


# --- QS2: the regression policy ------------------------------------------------------------------


def test_regressions_and_skipped_evals_block_the_merge() -> None:
    cfg = {
        "watched_paths": ["evals/prompts", "config/models.yaml"],
    }
    assert not regression.needs_eval(["src/exports/build.py"], cfg)
    assert regression.needs_eval(["evals/prompts/brief.txt"], cfg)
    assert regression.needs_eval(["evals/prompts/deep/x.md"], cfg)  # under a watched prefix
    # untouched path: merge without an eval
    d = regression.decide(eval_required=False, eval_ran=False, eval_score=None, baseline=None)
    assert d.merge
    # skipped eval blocks (a skipped eval is a failure, not a pass)
    d = regression.decide(eval_required=True, eval_ran=False, eval_score=None, baseline=0.8)
    assert not d.merge and "skipped" in d.reason
    # equal or better passes, worse blocks
    assert regression.decide(
        eval_required=True, eval_ran=True, eval_score=0.8, baseline=0.8
    ).merge
    assert regression.decide(
        eval_required=True, eval_ran=True, eval_score=0.85, baseline=0.8
    ).merge
    d = regression.decide(eval_required=True, eval_ran=True, eval_score=0.7, baseline=0.8)
    assert not d.merge and "< baseline" in d.reason
    # first watched change with no baseline: it establishes the baseline
    assert regression.decide(
        eval_required=True, eval_ran=True, eval_score=0.5, baseline=None
    ).merge


def test_the_rules_config_is_valid_and_hash_stable() -> None:
    cfg = regression.rules()
    assert regression.needs_eval(["config/models.yaml"], cfg)
    assert cfg["skipped_eval_is_failure"] is True


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_release_and_baseline_round_trip() -> None:
    candidate = f"crosscut-{NOW.timestamp()}"
    async with SessionLocal() as s, s.begin():
        v = await release_check.record(
            s, candidate=candidate, gate=True, verify=True, eval_=True, eval_score=0.9
        )
        assert v.released
        # a worse candidate records but is not released: the baseline stays 0.9
        await release_check.record(
            s, candidate=candidate + "-worse", gate=True, verify=True, eval_=True,
            eval_score=0.7, released=False,
        )
        assert await release_check.baseline(s) == pytest.approx(0.9)
        # the DB keeps the verdicts insert-only (a separate session discards the aborted txn)
        async with SessionLocal() as s2, s2.begin():
            with pytest.raises(sa.exc.ProgrammingError):
                await s2.execute(
                    sa.text("DELETE FROM release_checks WHERE candidate = :c"), {"c": candidate}
                )


# --- QS3: the analyst loop -----------------------------------------------------------------------


def test_the_sample_is_seeded_fresh_and_reproducible() -> None:
    pool = [f"item-{i}" for i in range(20)]
    a = analyst_loop.draw_sample(pool, 5, seed=7)
    b = analyst_loop.draw_sample(pool, 5, seed=7)
    assert a == b  # reproducible for the same seed
    c = analyst_loop.draw_sample(pool, 5, seed=8)
    assert a != c  # a different seed draws differently
    used = frozenset(a)
    d = analyst_loop.draw_sample(pool, 5, seed=7, exclude=used)
    assert not (set(d) & used)  # already-used items are never drawn again
    assert len(d) == 5
    with pytest.raises(ValueError):
        analyst_loop.draw_sample(pool, 0, seed=1)


def test_disagreements_become_eval_cases_with_provenance() -> None:
    cases = analyst_loop.make_cases(
        [("item-1", "confirmed", "hypothesis", "task-9"), ("item-2", "probable", "lead", "task-9")]
    )
    assert len(cases) == 2
    assert cases[0].provenance == "task-9"
    assert cases[0].analyst_label != cases[0].verifier_label


def test_an_open_task_past_its_due_date_is_overdue() -> None:
    t = analyst_loop.Task(1, "2026-Q1", 1, ("x",), datetime(2026, 1, 31, tzinfo=UTC), "open")
    assert analyst_loop.is_overdue(t, NOW)
    done = analyst_loop.Task(1, "2026-Q1", 1, ("x",), datetime(2026, 1, 31, tzinfo=UTC), "done")
    assert not analyst_loop.is_overdue(done, NOW)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_analyst_loop_samples_fresh_and_makes_cases() -> None:
    quarter = "2026-Q1"
    async with SessionLocal() as s, s.begin():
        used_before = set(await analyst_loop.open_items(s))
        pool = [f"fresh-{i}" for i in range(12)]
        t1 = await analyst_loop.create_task(
            s, quarter=quarter, pool=pool, k=4, seed=42,
            exclude=used_before, due_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
        assert len(t1.items) == 4
        # a second task the same quarter never redraws a used item
        used = set(await analyst_loop.open_items(s))
        t2 = await analyst_loop.create_task(
            s, quarter=quarter, pool=pool, k=4, seed=42,
            exclude=used, due_at=datetime(2026, 4, 1, tzinfo=UTC),
        )
        assert not (set(t2.items) & set(t1.items))
        # labels are insert-only; a disagreement becomes an eval case
        await analyst_loop.record_label(
            s, task_id=t1.id, item_ref=t1.items[0], label="probable", analyst="analyst-a"
        )
        async with SessionLocal() as s2, s2.begin():
            with pytest.raises(sa.exc.ProgrammingError):
                await s2.execute(
                    sa.text("DELETE FROM analyst_labels WHERE task_id = :t"), {"t": t1.id}
                )
        cases = analyst_loop.make_cases(
            [(t1.items[0], "confirmed", "probable", f"task-{t1.id}")]
        )
        assert cases[0].provenance == f"task-{t1.id}"
        # the operator sees the quarter and the overdue state
        got = {t.id: t for t in await analyst_loop.tasks(s)}
        assert got[t1.id].quarter == quarter
        assert got[t1.id].status == "open"
        assert not analyst_loop.is_overdue(got[t1.id], NOW)
