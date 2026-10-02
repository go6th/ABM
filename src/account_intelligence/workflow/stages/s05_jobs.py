"""Thin jobs stage adapter; production composition is intentionally explicit."""

from typing import Any

from account_intelligence.workflow.ports import NotConfigured
from account_intelligence.workflow.state import RunState


async def s05_jobs(state: RunState) -> dict[str, Any]:
    """Fail closed until real job adapters and coverage evidence are composed."""
    if not state.get("run_id"):
        raise ValueError("jobs stage requires trusted run id")
    raise NotConfigured("jobs stage adapters are not configured")
