"""Subprocess worker for the kill-mid-file test: reads one document and dies with
``os._exit(137)`` when it is about to write its (K+1)-th passage batch, that is right after the
K-th batch was committed (SIGKILL semantics: no cleanup runs, the part's lease stays held).

Usage: python -m tests.documents.kill_worker <tenant> <document> <batches> <blob_root>
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any
from uuid import UUID


async def main(tenant: str, document: str, batches: str, root: str) -> None:
    from documents import repo
    from tests.documents.world import service

    svc = service(Path(root))
    svc.cfg = svc.c.model_copy(
        update={"parsing": svc.c.parsing.model_copy(update={"part_bytes": 16384})}
    )
    svc.worker = "killed-worker"
    real = repo.write_passages
    seen = {"n": 0}

    async def write(*args: Any, **kwargs: Any) -> None:
        if seen["n"] >= int(batches):
            os._exit(137)
        seen["n"] += 1
        await real(*args, **kwargs)

    repo.write_passages = write  # type: ignore[assignment]
    svc.cfg = svc.c.model_copy(
        update={"parsing": svc.c.parsing.model_copy(update={"write_batch": 200})}
    )
    await svc.process(UUID(tenant), UUID(document))
    os._exit(0)


if __name__ == "__main__":
    from tests.scope import act_as_test_system

    act_as_test_system()
    asyncio.run(main(*sys.argv[1:5]))
