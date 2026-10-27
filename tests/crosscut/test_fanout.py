"""Bounded fan-out: independent provider calls run side by side, results keep item order, and a
started call is never cancelled for a sibling's failure (a cancelled paid call is paid twice)."""

from __future__ import annotations

import asyncio
import time

import pytest

from crosscut.fanout import fan_out, fan_out_all


@pytest.mark.asyncio
async def test_calls_overlap_and_results_keep_item_order() -> None:
    async def call(n: int) -> int:
        await asyncio.sleep(0.05 * (10 - n) / 10)  # later items finish first
        return n * 2

    started = time.monotonic()
    out = await fan_out_all(list(range(10)), call, limit=5)
    elapsed = time.monotonic() - started
    assert out == [n * 2 for n in range(10)]
    assert elapsed < 0.2, elapsed  # one at a time is ~0.28 s


@pytest.mark.asyncio
async def test_no_more_than_limit_calls_are_in_flight() -> None:
    live = peak = 0

    async def call(_: int) -> None:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1

    await fan_out_all(list(range(20)), call, limit=4)
    assert peak == 4


@pytest.mark.asyncio
async def test_a_started_call_finishes_when_a_sibling_fails_and_the_first_error_is_raised() -> None:
    finished: list[int] = []

    async def call(n: int) -> int:
        if n == 1:
            raise ValueError("first by item order")
        await asyncio.sleep(0.05)
        finished.append(n)
        if n == 3:
            raise KeyError("later")
        return n

    with pytest.raises(ValueError, match="first by item order"):
        await fan_out_all([0, 1, 2, 3], call, limit=4)
    assert sorted(finished) == [0, 2, 3]  # nothing in flight was cancelled


@pytest.mark.asyncio
async def test_stop_prevents_new_calls_and_marks_them_none() -> None:
    stopped = False
    started: list[int] = []

    async def call(n: int) -> int:
        nonlocal stopped
        started.append(n)
        stopped = True
        await asyncio.sleep(0.01)
        return n

    out = await fan_out([0, 1, 2, 3], call, limit=1, stop=lambda: stopped)
    assert started == [0] and out == [0, None, None, None]


@pytest.mark.asyncio
async def test_a_returned_exception_is_a_result_not_a_failure() -> None:
    async def call(n: int) -> Exception:
        return ValueError(str(n))

    out = await fan_out_all([1, 2], call)
    assert [str(e) for e in out] == ["1", "2"]
