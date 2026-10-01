"""src/orchestrator/functions.py - Inngest is only the outer loop: trigger, slice, wait, notify."""

from __future__ import annotations

import datetime
import os
from uuid import UUID

import inngest
from pydantic import BaseModel, ValidationError

from src.agents.runner import execute_slice

client = inngest.Inngest(app_id="ai-saas", is_production=os.getenv("ENV") == "prod")
MAX_SLICES = int(os.getenv("MAX_SLICES", "60"))  # 60 x 240 s = 4 h hard ceiling


class RunEvent(BaseModel):
    tenant_id: UUID
    run_id: UUID


@client.create_function(
    fn_id="run-research",
    trigger=inngest.TriggerEvent(event="research/run.requested"),
    retries=2,  # infrastructure failures only; node retries live in LangGraph
    concurrency=[
        inngest.Concurrency(
            limit=int(os.getenv("TENANT_CONCURRENCY", "5")), key="event.data.tenant_id"
        )
    ],  # per-tenant fairness
)
async def run_research(ctx: inngest.Context) -> dict[str, object]:
    try:
        event = RunEvent.model_validate(ctx.event.data)
    except ValidationError as error:
        raise ValueError("invalid research run event") from error
    step = ctx.step
    out: dict[str, object] = {"done": False}
    for i in range(MAX_SLICES):
        out = await step.run(
            f"slice-{i}",
            lambda: execute_slice(event.tenant_id, event.run_id),
        )
        if out.get("done"):
            break
        if out.get("awaiting"):
            await step.wait_for_event(
                f"approval-{i}",
                event="research/run.approved",
                if_exp="event.data.run_id == async.data.run_id",
                timeout=datetime.timedelta(hours=24),
            )
    else:
        out = {"done": True, "status": "FAILED"}  # ceiling reached: settle + refund in finalizer
    await step.send_event(
        "emit-completed",
        inngest.Event(
            name="research/run.completed",
            data={
                "tenant_id": str(event.tenant_id),
                "run_id": str(event.run_id),
            },
        ),
    )
    return out
