"""Subprocess worker for the kill-mid-parse test: processes one file and dies with
``os._exit(137)`` right after the K-th unit is committed (SIGKILL semantics: no cleanup runs).

Usage: python -m tests.research_files.kill_worker <tenant> <file> <kill_after> <blob_root>
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID


async def main(tenant: str, file_id: str, kill_after: str, root: str) -> None:
    from tests.research_files.world import config, services

    svc = services(Path(root), cfg=config(parsing={"rows_per_unit": 500}), worker="killed-worker")

    async def die(seq: int) -> None:
        if seq >= int(kill_after):
            os._exit(137)

    await svc.processor(after_unit=die).process(UUID(tenant), UUID(file_id))
    os._exit(0)


if __name__ == "__main__":
    from tests.scope import act_as_test_system

    act_as_test_system()
    asyncio.run(main(*sys.argv[1:5]))
