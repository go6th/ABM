"""Subprocess for the tracker SIGKILL test (spec 9.6).

Usage: python -m tests.tracker.kill_worker <mode> <tenant> <account> <run> <domain> <call_ref>
``mid_txn``: records the outcome (row UPDATE and event INSERT) inside the transaction, then dies
with ``os._exit(137)`` before the commit. ``after_commit``: records and commits, then dies.
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID

from account_intelligence.db.session import tenant_txn
from account_intelligence.tracker.model import Actor
from tests.tracker.world import World, tracker


async def main(mode: str, tenant: str, account: str, run: str, domain: str, ref: str) -> None:
    w = World(UUID(tenant), UUID(account), UUID(run), domain)
    async with tenant_txn(w.tenant) as session:
        await tracker(session).record_outcome(
            w.tenant, w.run, "company_news", [ref], Actor.STAGE, stage="news"
        )
        if mode == "mid_txn":
            os._exit(137)
    os._exit(137)


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:7]))
