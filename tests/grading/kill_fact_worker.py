"""Subprocess for kill tests: writes a graded fact and dies at a chosen point.

Usage: python -m tests.grading.kill_fact_worker <mode> <tenant> <account> <run>
mode ``mid_txn``: inserts the fact row inside the transaction, then ``os._exit(137)`` before
the evidence rows and the commit. mode ``after_commit``: records the fact fully, then dies.
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID

from sqlalchemy import text

from account_intelligence.db.session import tenant_txn
from account_intelligence.grading import repo
from tests.grading.db_world import graded_confirmed, run_info


async def main(mode: str, tenant: str, account: str, run: str) -> None:
    info = run_info(UUID(tenant), UUID(account), UUID(run))
    claim = graded_confirmed()
    if mode == "after_commit":
        await repo.record_graded_fact(tenant_txn, info, claim)
        os._exit(137)
    async with tenant_txn(info.tenant_id) as s:
        await repo._insert_fact(s, info, claim)
        await s.execute(text("SELECT 1"))
        os._exit(137)


if __name__ == "__main__":
    asyncio.run(main(*sys.argv[1:5]))
