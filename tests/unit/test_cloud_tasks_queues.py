"""The Cloud Tasks scheduler routes each task to its own queue with an explicit dispatch deadline
(three queues: runs, documents, jobs; deadlines within the service's 30-minute maximum), and the
Cloud SQL engine honours the pool bounds of the connection budget."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.platform.gcp.adapters import CloudTasksScheduler


class Client:
    """A Cloud Tasks client stand-in that records every created task."""

    def __init__(self) -> None:
        self.created: list[tuple[str, Any]] = []

    def task_path(self, project: str, location: str, queue: str, name: str) -> str:
        return f"projects/{project}/locations/{location}/queues/{queue}/tasks/{name}"

    def queue_path(self, project: str, location: str, queue: str) -> str:
        return f"projects/{project}/locations/{location}/queues/{queue}"

    def create_task(self, parent: str, task: Any) -> None:
        self.created.append((parent, task))


def test_each_task_goes_to_its_queue_with_a_deadline() -> None:
    pytest.importorskip("google.cloud.tasks_v2")
    client = Client()
    s = CloudTasksScheduler(
        client,
        "p",
        "r",
        "runs",
        "https://worker.example",
        "tasks@p.iam.example",
        "aud",
        documents_queue="documents",
        jobs_queue="jobs",
    )
    t = uuid4()

    async def enqueue() -> None:
        await s.enqueue_slice(t, uuid4(), 0)
        await s.enqueue_document(t, uuid4())
        await s.enqueue_research_job(t, uuid4())
        await s.enqueue_download(t, uuid4())

    asyncio.run(enqueue())
    seen = [
        (
            parent.rsplit("/", 1)[1],
            task.name.split("/queues/")[1].split("/")[0],
            task.dispatch_deadline.total_seconds(),
        )
        for parent, task in client.created
    ]
    assert seen == [
        ("runs", "runs", 1800),
        ("documents", "documents", 1800),
        ("jobs", "jobs", 600),
        ("jobs", "jobs", 600),
    ]
    assert all(d <= 1800 for *_, d in seen)


def test_one_queue_still_works_when_the_others_are_unset() -> None:
    pytest.importorskip("google.cloud.tasks_v2")
    client = Client()
    s = CloudTasksScheduler(client, "p", "r", "runs", "https://w", "sa@x", "aud")
    asyncio.run(s.enqueue_document(uuid4(), uuid4()))
    assert client.created[0][0].endswith("/queues/runs")


def test_the_cloud_sql_engine_uses_the_configured_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("google.cloud.sql.connector")
    import google.cloud.sql.connector as connector_module

    from account_intelligence.platform.gcp import adapters

    async def fake_connector(**_: Any) -> object:
        return object()

    monkeypatch.setattr(connector_module, "create_async_connector", fake_connector)
    engine, _ = asyncio.run(
        adapters.cloud_sql_engine("p:r:i", "u", "d", pool_size=3, max_overflow=2)
    )
    assert engine.pool.size() == 3 and engine.pool._max_overflow == 2
