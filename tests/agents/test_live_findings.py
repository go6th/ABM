"""Findings reach the store while the run is still reading (audit 2026-10-21): each source group's
facts are graded and saved as soon as that group's last extraction call returns, every stored
payload is read once, records pulled by the account's own domain are kept, and a failure in one
group cancels the others instead of leaving them paying in the background."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from agents.guards import ACCOUNT_KEYED_NEEDS
from llm.errors import ProviderCreditsExhausted
from tests.agents.conftest import Harness

pytestmark = pytest.mark.asyncio


def _group(payload: Mapping[str, Any]) -> str:
    return str(payload.get("group", ""))


async def test_a_finished_group_is_saved_while_another_is_still_reading(h: Harness) -> None:
    """The web search group of pass 2 waits until another pass 2 group's facts are in the store;
    before this change every fact waited for the whole node, so the wait timed out."""
    assert h.llm is not None
    real = h.llm.call
    seen_before_slow: list[int] = []

    async def call(
        task_kind: str, prompt_text: str, prompt_hash: str, payload: Any, output: Any, ctx: Any
    ) -> Any:
        if task_kind == "abm_pass2_extract" and _group(payload) == "web_search":
            for _ in range(200):  # up to 2 s for another group to be saved
                rows = {f.row_key for f in h.grading.facts_by_run[ctx.run_id].values()}
                if rows & {"company_news", "technology_detections", "job_postings_12_months"}:
                    break
                await asyncio.sleep(0.01)
            seen_before_slow.append(len(h.grading.facts_by_run[ctx.run_id]))
        return await real(task_kind, prompt_text, prompt_hash, payload, output, ctx)

    h.llm.call = call  # type: ignore[method-assign]
    _, state = await h.run("r")
    assert state["settled"] is True
    assert seen_before_slow, "the pass 2 web search group was not extracted"
    assert all(n > 0 for n in seen_before_slow), "facts waited for the slowest group"


async def test_every_stored_payload_reaches_the_extractor_once(h: Harness) -> None:
    assert h.llm is not None
    real = h.llm.call
    refs: list[str] = []

    async def call(
        task_kind: str, prompt_text: str, prompt_hash: str, payload: Any, output: Any, ctx: Any
    ) -> Any:
        if task_kind.endswith("_extract"):
            refs.extend(p["payload_ref"] for p in payload["payloads"])
        return await real(task_kind, prompt_text, prompt_hash, payload, output, ctx)

    h.llm.call = call  # type: ignore[method-assign]
    await h.run("r")
    assert refs
    assert len(refs) == len(set(refs))


async def test_records_pulled_by_the_accounts_own_domain_are_never_off_target(
    h: Harness,
) -> None:
    """A feed record's text rarely names the account; the off-target check skips such needs."""
    feeds = [k for k, row in h.cfg.plan.items() if row.need in ACCOUNT_KEYED_NEEDS]
    assert "technology_detections" in feeds and "company_news" in feeds
    original = h.payloads.view

    async def nameless(tenant: Any, ref: str) -> Any:
        view = await original(tenant, ref)
        if view.row_key in feeds:
            return view.model_copy(
                update={"url": "https://feed.example/r", "text": "Uses Tableau."}
            )
        return view

    h.payloads.view = nameless  # type: ignore[method-assign]
    _, state = await h.run("r")
    assert state["counters"].get("drop_off_target", 0) == 0


async def test_a_failing_group_cancels_the_others_and_keeps_the_saved_ones(h: Harness) -> None:
    assert h.llm is not None
    real = h.llm.call
    after_failure: list[str] = []
    failed = asyncio.Event()

    async def call(
        task_kind: str, prompt_text: str, prompt_hash: str, payload: Any, output: Any, ctx: Any
    ) -> Any:
        if task_kind == "abm_pass2_extract":
            group = _group(payload)
            # the posting rows are the job pipeline's since 2026-10-24 (no scrape group here):
            # the feed group fails, the web search group waits for it
            if group == "structured_signals":
                failed.set()
                raise ProviderCreditsExhausted("credit balance too low")
            if group == "web_search":
                await failed.wait()
                await asyncio.sleep(0.05)
                after_failure.append(group)  # only reached if the failure did not cancel it
        return await real(task_kind, prompt_text, prompt_hash, payload, output, ctx)

    h.llm.call = call  # type: ignore[method-assign]
    record, state = await h.run("r")
    await asyncio.sleep(0.2)  # a call left running in the background would finish by now
    assert state["error_code"] == "model_credits_exhausted"  # the real error, not a group
    assert after_failure == []
    assert h.grading.facts_by_run[record.run_id]  # pass 1 findings stay saved
