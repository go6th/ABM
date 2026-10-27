"""A research reads and pulls its independent sources side by side (staging 2026-10-27).

The pull phase made its provider calls one at a time: 33 planned rows and three or more searches
each, at about three seconds a search. Each call is slowed here, and the whole run must take well
under what one-at-a-time calls would, with the same outcome."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from tests.agents.conftest import Harness

DELAY = 0.03


def slow(target: Any, name: str, delay: float) -> list[int]:
    """Wrap ``target.name`` so each call takes ``delay`` seconds; the list counts the calls."""
    inner = getattr(target, name)
    calls = [0]

    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        await asyncio.sleep(delay)
        return await inner(*args, **kwargs)

    setattr(target, name, wrapped)
    return calls


@pytest.mark.asyncio
async def test_the_pull_phase_overlaps_its_provider_calls(h: Harness) -> None:
    h.grant_consent()
    calls = slow(h.provider, "call", DELAY)
    started = time.monotonic()
    _, state = await h.run("speed-1")
    elapsed = time.monotonic() - started
    assert calls[0] >= 30, calls[0]
    # one at a time the provider calls alone take calls x DELAY
    assert elapsed < calls[0] * DELAY * 0.5, (elapsed, calls[0] * DELAY)
    assert state["outcome"] in ("completed", "partial")
