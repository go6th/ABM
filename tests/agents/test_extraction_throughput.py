"""The reading step of a large account fits inside one slice (staging 2026-10-21, Tolmar).

Extraction runs one Message Batch per call and keeps ``EXTRACT_CONCURRENCY`` of them in flight
per step. A batch takes minutes at the provider, so the step's duration is the calls divided by
the concurrency times the batch time. At 8 in flight, Tolmar's 121 calls took ~75 minutes and a
step that cannot finish in one slice ran across five. The sizing is checked here against the
configured slice, and the polling against the provider's lowest batch request rate."""

from __future__ import annotations

import asyncio
import math

import pytest

from account_intelligence.platform.llm import (
    BATCH_REQUESTS_PER_MINUTE,
    RequestPacer,
    batch_request,
    load_batch_config,
)
from account_intelligence.workflow.spec import load_spec
from agents.graph import EXTRACT_CONCURRENCY

TOLMAR_CALLS = 121  # extraction calls of the largest research seen (896 job posting rows)
BATCH_SECONDS = 285  # mean batch time seen on staging: 32 calls in 19 minutes at 8 in flight
LOWEST_BATCH_REQUESTS_PER_MINUTE = 50  # the provider's lowest tier for batch endpoints
SLICE_MARGIN = 0.8  # the reading step should take at most this share of a slice
WORKER_RUNS_AT_ONCE = 4  # Cloud Run worker concurrency (infra/stack/main.tf)


def test_the_largest_research_seen_reads_inside_one_slice() -> None:
    slice_seconds = float(load_spec().settings["slice_seconds"])
    rolling = TOLMAR_CALLS / EXTRACT_CONCURRENCY * BATCH_SECONDS
    assert rolling <= slice_seconds * SLICE_MARGIN, (rolling, slice_seconds)
    # whole waves, the worst case when the calls start together
    waves = math.ceil(TOLMAR_CALLS / EXTRACT_CONCURRENCY) * BATCH_SECONDS
    assert waves <= slice_seconds, (waves, slice_seconds)


def test_batch_polling_stays_under_the_providers_request_rate() -> None:
    """One run's polling fits the rate on its own, so the pacer never slows a lone run; with
    several runs on one worker the process-wide pacer holds the rate (the poll interval alone
    cannot: at Cloud Run concurrency 4 the four runs would ask ~170 times a minute)."""
    poll = load_batch_config().poll_seconds
    requests_per_minute = EXTRACT_CONCURRENCY * 60 / poll
    assert requests_per_minute <= LOWEST_BATCH_REQUESTS_PER_MINUTE, requests_per_minute
    assert BATCH_REQUESTS_PER_MINUTE <= LOWEST_BATCH_REQUESTS_PER_MINUTE
    # under the pacer every batch is still polled well inside the batch time, so the step's
    # duration stays the batch time, not the polling
    paced_interval = WORKER_RUNS_AT_ONCE * EXTRACT_CONCURRENCY * 60 / BATCH_REQUESTS_PER_MINUTE
    assert paced_interval <= BATCH_SECONDS, paced_interval


@pytest.mark.asyncio
async def test_the_pacer_spaces_requests_process_wide() -> None:
    pacer = RequestPacer(600)  # 0.1 s apart
    loop = asyncio.get_running_loop()
    started = loop.time()
    await asyncio.gather(*(pacer.wait() for _ in range(4)))
    assert loop.time() - started >= 0.3


@pytest.mark.asyncio
async def test_a_429_waits_and_retries_but_any_other_error_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import account_intelligence.platform.llm as llm_mod

    monkeypatch.setattr(llm_mod, "BATCH_PACER", RequestPacer(0))
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    calls = 0

    class RateLimitError(Exception):
        status_code = 429

    async def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RateLimitError()
        return "ok"

    assert await batch_request(flaky, what="retrieve", model="m", tier="t") == "ok"
    assert calls == 3 and slept == [5.0, 10.0]

    async def broken() -> str:
        raise ValueError("not a rate limit")

    with pytest.raises(ValueError):
        await batch_request(broken, what="retrieve", model="m", tier="t")
