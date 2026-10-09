"""The one-run-per-container job loop (account_intelligence.worker)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from account_intelligence.worker.__main__ import main
from account_intelligence.worker.job import LoopScheduler, drive

T, R = uuid4(), uuid4()


def scripted(outcomes: list[str]):  # type: ignore[no-untyped-def]
    calls: list[int] = []

    async def run(scheduler: LoopScheduler, slice_no: int) -> tuple[str, str]:
        calls.append(slice_no)
        outcome = outcomes.pop(0)
        if outcome == "continue":
            await scheduler.enqueue_slice(T, R, slice_no + 1)
        return outcome, "RUNNING" if outcome in ("continue", "busy") else "COMPLETED"

    return run, calls


async def nosleep(_: float) -> None:
    return None


@pytest.mark.asyncio
async def test_job_runs_slices_until_terminal() -> None:
    run, calls = scripted(["continue", "continue", "done"])
    result = await drive(run, 3, max_slices=10, sleep=nosleep)
    assert calls == [3, 4, 5] and result.outcome == "done" and result.slices == 3


@pytest.mark.asyncio
async def test_job_stops_when_waiting_for_a_person() -> None:
    run, calls = scripted(["continue", "awaiting", "continue"])
    result = await drive(run, 0, max_slices=10, sleep=nosleep)
    assert calls == [0, 1] and result.outcome == "awaiting"


@pytest.mark.asyncio
async def test_job_never_exceeds_max_slices() -> None:
    run, calls = scripted(["continue"] * 50)
    result = await drive(run, 0, max_slices=4, sleep=nosleep)
    assert len(calls) == 4 and result.slices == 4


@pytest.mark.asyncio
async def test_busy_run_backs_off_then_gives_up() -> None:
    waits: list[float] = []

    async def record(seconds: float) -> None:
        waits.append(seconds)

    run, _ = scripted(["busy", "busy", "done"])
    assert (await drive(run, 0, max_slices=5, sleep=record)).outcome == "done"
    assert waits == [2.0, 4.0]
    run, _ = scripted(["busy"] * 10)
    with pytest.raises(RuntimeError, match="another worker"):
        await drive(run, 0, max_slices=5, busy_retries=2, sleep=record)


@pytest.mark.asyncio
async def test_a_slice_that_queues_nothing_ends_the_job() -> None:
    run, calls = scripted(["noop"])
    assert (await drive(run, 0, max_slices=5, sleep=nosleep)).outcome == "noop" and calls == [0]


@pytest.mark.asyncio
async def test_loop_scheduler_is_idempotent_per_slice() -> None:
    s = LoopScheduler()
    await s.enqueue_slice(T, R, 1)
    await s.enqueue_slice(T, R, 1)
    at = datetime.now(UTC)
    await s.schedule_timeout(T, R, at)
    await s.schedule_timeout(T, R, datetime.now(UTC))
    assert s.pending == [1] and s.timeouts[R] == at


def test_entry_rejects_bad_arguments() -> None:
    assert main(["--tenant-id", "not-a-uuid", "--run-id", str(R)]) == 2
    assert main([]) == 2


def test_entry_refuses_an_invalid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    assert main(["--tenant-id", str(T), "--run-id", str(R)]) == 2
