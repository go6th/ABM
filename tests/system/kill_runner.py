"""Subprocess worker for the D-18 kill/resume spike: runs one run's slices and dies with
``os._exit(137)`` right after the K-th provider response (between a paid response and its ledger
write, the most dangerous point). SYNTHETIC world (tests/system/world.py).

Usage: python -m tests.system.kill_runner <tenant> <run> <kill_at> <payload_dir>
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID

from tests.connectors.conftest import FakeClock, FakeProvider, FakeSleep, Harness
from tests.system.world import Script, drive


async def main(tenant: str, run: str, kill_at: str, payload_dir: str) -> None:
    clock = FakeClock()
    from account_intelligence.connectors.base import ProviderCode
    from account_intelligence.platform.hermetic import LocalPayloadStore

    harness = Harness(
        clock,
        FakeSleep(clock),
        {c: FakeProvider(clock) for c in ProviderCode},
        LocalPayloadStore(payload_dir),
        {},
    )  # type: ignore[arg-type]
    seen = 0

    async def after_response() -> None:
        nonlocal seen
        seen += 1
        if seen >= int(kill_at):
            os._exit(137)

    await drive(
        harness,
        Script(),
        UUID(tenant),
        UUID(run),
        worker_id="killed-worker",
        after_response_hook=after_response,
    )
    os._exit(0)


if __name__ == "__main__":
    from tests.scope import act_as_test_system

    act_as_test_system()
    asyncio.run(main(*sys.argv[1:5]))
