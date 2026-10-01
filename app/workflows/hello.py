"""Hello-world workflow (roadmap s6 deliverable: three environments running a hello-world workflow).

One workflow, one activity. The activity loads the rule bundle so a green run also proves the
worker image ships the versioned config. Real runs follow the same pattern: workflow code is
deterministic orchestration, every side effect (broker, model, storage) is an activity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.rules import default_rules


@dataclass(frozen=True)
class HelloInput:
    name: str


@dataclass(frozen=True)
class HelloResult:
    greeting: str
    config_version: str


@activity.defn(name="hello_activity")
async def hello_activity(params: HelloInput) -> HelloResult:
    name = params.name.strip()
    if not name:
        raise ValueError("name must not be empty")
    return HelloResult(greeting=f"Hello, {name}", config_version=default_rules().version)


@workflow.defn(name="HelloWorkflow")
class HelloWorkflow:
    @workflow.run
    async def run(self, params: HelloInput) -> HelloResult:
        return await workflow.execute_activity(
            hello_activity,
            params,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3, non_retryable_error_types=["ValueError"]),
        )
