"""Follow-up chat reads the one model layer (owner decision 2026-10-17): the conversation's
analysis model — the thread's kept model while it is usable (Kimi K3 included), else the
user's current one — instead of the hardcoded follow_up_chat phase model (Fable)."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from account_intelligence.chat.store import create_thread
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import models as M

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

KEYS = {"ANTHROPIC_API_KEY": "x", "MOONSHOT_API_KEY": "y"}  # both providers "set up" here


async def test_the_thread_keeps_its_analysis_model_kimi_included(world: dict) -> None:
    tenant = world["a"]
    thread = await create_thread(tenant, "priya-test")
    async with tenant_txn(tenant) as session:
        await session.execute(
            text("UPDATE chat_threads SET analysis_model = 'kimi-k3' WHERE id = :t"),
            {"t": thread},
        )
        kept = await M.conversation_model(session, "priya-test", None, thread, env=KEYS)
        assert kept == "kimi-k3"

    plain = await create_thread(tenant, "priya-test")
    async with tenant_txn(tenant) as session:
        # no kept model on the thread and no saved preference: the catalogue's analysis default
        current = await M.conversation_model(session, "priya-test", None, plain, env=KEYS)
        assert current == "claude-fable-5-1"
