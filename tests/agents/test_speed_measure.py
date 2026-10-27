"""Wall-clock of one research on the harness with every outside call slowed (speed audit
2026-10-27). Each slowed port counts its calls, so the serial time (calls x delay) is known and
the run's elapsed time shows how much of it overlapped. Printed, and bounded."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from tests.agents.conftest import Harness

PROVIDER = 0.03  # a search or a page read
PIPELINE = 0.25  # one code pipeline (jobs, tech, people)
VIEW = 0.002  # one payload read back from the store
BUILD = 0.15  # one file set built


def slow(target: Any, name: str, delay: float) -> dict[str, float]:
    inner = getattr(target, name)
    stats = {"calls": 0.0, "busy": 0.0}

    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        stats["calls"] += 1
        started = time.monotonic()
        await asyncio.sleep(delay)
        try:
            return await inner(*args, **kwargs)
        finally:
            stats["busy"] += time.monotonic() - started

    setattr(target, name, wrapped)
    return stats


@pytest.mark.asyncio
async def test_a_research_overlaps_its_outside_waits(h: Harness) -> None:
    h.grant_consent()
    provider = slow(h.provider, "call", PROVIDER)
    jobs = slow(h.pipelines, "run_jobs", PIPELINE)
    tech = slow(h.pipelines, "write_detections", PIPELINE)
    people = slow(h.pipelines, "run_people", PIPELINE)
    views = slow(h.payloads, "view", VIEW)
    builds = slow(h.builder, "build", BUILD)
    started = time.monotonic()
    _, state = await h.run("speed-measure")
    elapsed = time.monotonic() - started
    assert state["outcome"] in ("completed", "partial"), state.get("error_code")
    serial = {
        "provider": provider["calls"] * PROVIDER,
        "pipelines": (jobs["calls"] + tech["calls"] + people["calls"]) * PIPELINE,
        "views": views["calls"] * VIEW,
        "builds": builds["calls"] * BUILD,
    }
    print(
        "\nspeed-measure elapsed %.2fs; serial waits %s; calls provider=%d pipelines=%d"
        " views=%d builds=%d"
        % (
            elapsed,
            {k: round(v, 2) for k, v in serial.items()},
            provider["calls"],
            jobs["calls"] + tech["calls"] + people["calls"],
            views["calls"],
            builds["calls"],
        )
    )
    # the pulls overlap (the morning's fan-out); the bound is loose on purpose, the number
    # printed above is the measurement
    assert elapsed < sum(serial.values())
