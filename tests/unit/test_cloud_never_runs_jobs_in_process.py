"""A cloud API process hands a research job to the worker queue and never runs it in-process
(fix prompt B, task 1; ADR-0109): with no queue wired, the job stays queued for ``runs.recovery``
instead of running inside the api (which no longer serves ``/tasks`` and has no lease loop)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from account_intelligence.replica import jobs


class Engine:
    """Records an in-process run (it must not happen in the cloud)."""

    def __init__(self) -> None:
        self.ran = 0


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", ["staging", "production"])
async def test_no_queue_in_the_cloud_leaves_the_job_queued(
    monkeypatch: pytest.MonkeyPatch, profile: str
) -> None:
    ran: list[object] = []

    async def run_job(*args: object) -> None:
        ran.append(args)

    monkeypatch.setenv("APP_ENV", profile)
    monkeypatch.setattr(jobs, "_scheduler", None)
    monkeypatch.setattr(jobs, "_engine", Engine())
    monkeypatch.setattr(jobs, "run_job", run_job)
    await jobs.run_or_enqueue(uuid4(), uuid4())  # does not raise: recovery re-enqueues it
    assert ran == []


@pytest.mark.asyncio
async def test_dev_and_test_still_run_in_process_without_a_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[object] = []

    async def run_job(*args: object) -> None:
        ran.append(args)

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(jobs, "_scheduler", None)
    monkeypatch.setattr(jobs, "_engine", Engine())
    monkeypatch.setattr(jobs, "run_job", run_job)
    await jobs.run_or_enqueue(uuid4(), uuid4())
    assert len(ran) == 1
