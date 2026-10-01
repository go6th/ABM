"""Hello-world Temporal workflow with one activity (roadmap s6 environments and CI).

The end-to-end test uses Temporal's time-skipping test server, which the SDK downloads on first
use and caches; no docker or external Temporal cluster is needed.
"""

from __future__ import annotations

import uuid

import pytest
from temporalio.client import WorkflowFailureError
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Worker

from app.rules import default_rules
from app.worker import build_worker, start_hello
from app.workflows import ACTIVITIES, WORKFLOWS, HelloInput, HelloWorkflow, hello_activity

TASK_QUEUE = "test-hello"


async def test_activity_greets_and_reports_config_version() -> None:
    result = await ActivityEnvironment().run(hello_activity, HelloInput(name="  Ada "))
    assert result.greeting == "Hello, Ada"
    assert result.config_version == default_rules().version


async def test_activity_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        await ActivityEnvironment().run(hello_activity, HelloInput(name="   "))


async def test_hello_workflow_end_to_end() -> None:
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        build_worker(env.client, TASK_QUEUE),
    ):
        result = await start_hello(env.client, TASK_QUEUE, "world")
    assert result.greeting == "Hello, world"
    assert result.config_version == default_rules().version


async def test_hello_workflow_does_not_retry_bad_input() -> None:
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(env.client, task_queue=TASK_QUEUE, workflows=WORKFLOWS, activities=ACTIVITIES),
    ):
        with pytest.raises(WorkflowFailureError):
            await env.client.execute_workflow(
                HelloWorkflow.run,
                HelloInput(name=""),
                id=f"hello-{uuid.uuid4()}",
                task_queue=TASK_QUEUE,
            )
