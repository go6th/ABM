"""Model replies are reused across runs of one tenant with the run id rewritten, logged with how
they were served, and never past their TTL or across tenants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from account_intelligence.platform.hermetic import MemoryPayloadStore, ScriptedLlm, SystemClock
from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.spec import load_spec
from llm.reply_cache import (
    RUN_PLACEHOLDER,
    InMemoryReplyCache,
    ReplyUsage,
    denormalise,
    normalise,
    price,
    reply_key,
)


def _deps(llm: ScriptedLlm, cache: InMemoryReplyCache, tenant, run=None, fresh=False) -> RunDeps:  # type: ignore[no-untyped-def]
    account = uuid4()
    return RunDeps(
        tenant,
        run or uuid4(),
        account,
        "acme.example",
        None,  # type: ignore[arg-type]
        llm,
        MemoryPayloadStore(),
        SystemClock(),
        load_spec(),
        run_input={"fresh": True} if fresh else {},
        reply_cache=cache,
    )


def test_normalisation_round_trips_the_run_id() -> None:
    run = uuid4()
    body = f"tenants/t/accounts/a/runs/{run}/raw/abc and {run}"
    assert str(run) not in normalise(body, run)
    assert normalise(body, run).count(RUN_PLACEHOLDER) == 2
    assert denormalise(normalise(body, run), run) == body
    assert reply_key("k", "t", 10, "s", "u") == reply_key("k", "t", 10, "s", "u")
    assert reply_key("k", "t", 10, "s", "u") != reply_key("k", "t", 11, "s", "u")


@pytest.mark.asyncio
async def test_a_reply_is_reused_across_runs_with_the_run_id_rewritten() -> None:
    tenant = uuid4()
    cache = InMemoryReplyCache()
    # the model echoes the payload reference it was given (as an extractor cites evidence)
    llm = ScriptedLlm(lambda tier, system, user: '{"cited": "' + user.split()[-1] + '"}')
    run1, run2 = uuid4(), uuid4()
    one = _deps(llm, cache, tenant, run1)
    first = await one.model("abm_pass1_extract", "sys", f"payload tenants/t/runs/{run1}/raw/x")
    assert f"runs/{run1}/" in first.text and len(llm.calls) == 1
    two = _deps(llm, cache, tenant, run2)
    second = await two.model("abm_pass1_extract", "sys", f"payload tenants/t/runs/{run2}/raw/x")
    assert len(llm.calls) == 1  # no model call
    assert second.text == first.text.replace(str(run1), str(run2))  # same output, this run's refs
    assert second.usage.input_tokens == 0 and second.extra.get("cached") is True
    served = [r["served_from"] for r in cache.rows]
    assert served == ["started", "model", "cache"]  # write-ahead row before the paid call
    assert cache.rows[0]["reply_text"] is None and cache.rows[0]["usage"].input_tokens == 0
    assert cache.rows[1]["reply_text"] and RUN_PLACEHOLDER in cache.rows[1]["reply_text"]
    # the reuse row keeps the (normalised) text, so a resume of run2 can reuse it too
    assert cache.rows[2]["reply_text"] == cache.rows[1]["reply_text"]


@pytest.mark.asyncio
async def test_reply_reuse_expires_and_stays_within_the_tenant() -> None:
    cache = InMemoryReplyCache(ttl_days=7)
    llm = ScriptedLlm(lambda *a: '{"ok": true}')
    tenant_a, tenant_b = uuid4(), uuid4()
    await _deps(llm, cache, tenant_a).model("abm_pass1_extract", "sys", "same")
    await _deps(llm, cache, tenant_b).model("abm_pass1_extract", "sys", "same")
    assert len(llm.calls) == 2  # tenant B never sees tenant A's reply
    await _deps(llm, cache, tenant_a).model("abm_pass1_extract", "sys", "same")
    assert len(llm.calls) == 2  # tenant A reuses its own
    for row in cache.rows:
        row["created_at"] -= timedelta(days=8)
    await _deps(llm, cache, tenant_a).model("abm_pass1_extract", "sys", "same")
    assert len(llm.calls) == 3  # expired: asked again


@pytest.mark.asyncio
async def test_a_fresh_run_and_a_non_cacheable_task_always_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    import llm.tasks as tasks

    # no configured kind is marked not cacheable since 2026-10-20 (token review item 11): the
    # re-read kind is marked so here to check the mechanism
    real = tasks.require
    monkeypatch.setattr(
        tasks,
        "require",
        lambda kind: replace(real(kind), cacheable=kind != "source_reread"),
    )
    cache = InMemoryReplyCache()
    llm = ScriptedLlm(lambda *a: '{"ok": true}')
    tenant = uuid4()
    await _deps(llm, cache, tenant).model("abm_pass1_extract", "sys", "same")
    await _deps(llm, cache, tenant, fresh=True).model("abm_pass1_extract", "sys", "same")
    assert len(llm.calls) == 2
    await _deps(llm, cache, tenant).model("source_reread", "sys", "same")
    await _deps(llm, cache, tenant).model("source_reread", "sys", "same")
    assert len(llm.calls) == 4
    assert {r["served_from"] for r in cache.rows} == {"started", "model"}


def test_price_uses_configured_prices_and_is_none_for_unknown_models() -> None:
    import yaml

    from account_intelligence.config import CONFIG_DIR

    raw = yaml.safe_load((CONFIG_DIR / "models.yaml").read_text())
    model = raw["allowlist"][0]
    per_million = Decimal(str(raw["models"][model]["pricing"]["input_usd_per_million"]))
    assert price(model, ReplyUsage(1_000_000, 0)) == per_million
    assert price("scripted-extractor", ReplyUsage(10, 10)) is None


@pytest.mark.asyncio
async def test_an_empty_reply_is_not_stored_for_reuse() -> None:
    cache = InMemoryReplyCache()
    answers = iter(["   ", '{"ok": true}'])
    llm = ScriptedLlm(lambda *a: next(answers))
    tenant = uuid4()
    await _deps(llm, cache, tenant).model("abm_pass1_extract", "sys", "same")
    await _deps(llm, cache, tenant).model("abm_pass1_extract", "sys", "same")
    model_rows = [r for r in cache.rows if r["served_from"] == "model"]
    assert len(llm.calls) == 2 and model_rows[0]["reply_text"] is None


def test_in_memory_cache_get_respects_now() -> None:
    cache = InMemoryReplyCache(ttl_days=1)
    import asyncio

    tenant = uuid4()
    asyncio.run(
        cache.record(
            tenant,
            uuid4(),
            "k",
            "t",
            "tier",
            "m",
            "model",
            ReplyUsage(1, 1),
            None,
            "x",
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    assert asyncio.run(cache.get(tenant, "k", datetime(2026, 1, 1, 12, tzinfo=UTC))) is not None
    assert asyncio.run(cache.get(tenant, "k", datetime(2026, 1, 3, tzinfo=UTC))) is None


@pytest.mark.asyncio
async def test_a_call_that_dies_leaves_its_started_row_and_is_never_reused() -> None:
    """Write-ahead: the process died inside the model call; the started row stays, carries no
    text and no tokens, and the next run asks the model again."""
    cache = InMemoryReplyCache()
    tenant = uuid4()

    def dying(*_: object) -> str:
        raise ConnectionError("process died mid-call")

    with pytest.raises(ConnectionError):
        await _deps(ScriptedLlm(dying), cache, tenant).model("abm_pass1_extract", "sys", "same")
    assert [r["served_from"] for r in cache.rows] == ["started"]
    llm = ScriptedLlm(lambda *a: '{"ok": true}')
    await _deps(llm, cache, tenant).model("abm_pass1_extract", "sys", "same")
    assert len(llm.calls) == 1  # the started row was not a hit


@pytest.mark.asyncio
async def test_a_resume_of_a_resume_reuses_what_the_resumed_run_reused() -> None:
    """Run C resumes run B, which itself resumed run A: B's replies were all reused from A, and
    C must reuse them from B instead of paying again (audit 2026-10-15)."""
    tenant = uuid4()
    cache = InMemoryReplyCache(ttl_days=0)  # no time-based reuse: only the resumed run counts
    llm = ScriptedLlm(lambda *a: '{"ok": true}')
    run_a, run_b, run_c = uuid4(), uuid4(), uuid4()
    await _deps(llm, cache, tenant, run_a).model("abm_pass1_extract", "sys", "same")
    b = _deps(llm, cache, tenant, run_b)
    b.run_input = {"resume_from": str(run_a), "fresh": True}
    await b.model("abm_pass1_extract", "sys", "same")
    c = _deps(llm, cache, tenant, run_c)
    c.run_input = {"resume_from": str(run_b), "fresh": True}
    third = await c.model("abm_pass1_extract", "sys", "same")
    assert len(llm.calls) == 1 and third.extra.get("cached") is True
