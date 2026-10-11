"""Chat answers are grounded in held workspace facts, never composed from general knowledge."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.chat.grounding import named_domains, workspace_context
from account_intelligence.chat.service import GROUNDING_MAX_CHARS, grounded_system_prompt

TENANT = uuid4()
HELD = SimpleNamespace(id=uuid4(), domain="acme.com", name="Acme Corp")
EMPTY = SimpleNamespace(id=uuid4(), domain="pilot-a-1.example", name=None)


def fake_txn(facts: dict[Any, list[Any]]) -> Any:
    calls: list[dict[str, Any]] = []

    class Session:
        async def execute(self, statement: Any, params: dict[str, Any]) -> Any:
            calls.append(params)
            assert params["t"] == TENANT
            rows = facts.get(params["a"], []) if "a" in params else [HELD, EMPTY]
            return SimpleNamespace(all=lambda: rows)

    @asynccontextmanager
    async def txn(tenant_id: Any):
        assert tenant_id == TENANT
        yield Session()

    txn.calls = calls  # type: ignore[attr-defined]
    return txn


def fact(key: str, value: str, url: str | None) -> Any:
    return SimpleNamespace(
        attribute_key=key,
        value_text=value,
        value_json=None,
        product_grade="confirmed",
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
        url=url,
        evidence_date=datetime(2026, 9, 30, tzinfo=UTC),
    )


def test_named_domains_are_lowercased_deduplicated_and_strip_www() -> None:
    assert named_domains(
        ["Quide/Ortho - quidelortho.com", "WWW.QuidelOrtho.com and acme.co.uk"]
    ) == [
        "quidelortho.com",
        "acme.co.uk",
    ]


@pytest.mark.asyncio
async def test_unheld_company_is_marked_not_held_and_gets_no_facts() -> None:
    txn = fake_txn({})
    block = await workspace_context(TENANT, ["Quide/Ortho - quidelortho.com"], txn=txn)
    assert "NOT held in this workspace: quidelortho.com" in block
    assert "acme.com (Acme Corp)" in block
    assert "Held facts for" not in block
    assert len(txn.calls) == 1


@pytest.mark.asyncio
async def test_named_account_facts_are_numbered_with_source_and_date() -> None:
    txn = fake_txn(
        {HELD.id: [fact("ceo", "Jane Doe", "https://acme.com/team"), fact("hq", "Austin", None)]}
    )
    block = await workspace_context(TENANT, ["Who leads Acme?"], txn=txn)
    assert (
        "[F1] ceo: Jane Doe | grade: confirmed | source: https://acme.com/team | as of 2026-09-30"
        in block
    )
    assert "[F2] hq: Austin | grade: confirmed | source: none recorded" in block
    assert "pilot-a-1.example" in block and "NOT held" not in block


@pytest.mark.asyncio
async def test_held_account_without_research_says_so_and_block_is_bounded() -> None:
    many = [fact(f"k{i}", "x" * 500, None) for i in range(40)]
    txn = fake_txn({EMPTY.id: [], HELD.id: many})
    block = await workspace_context(TENANT, ["pilot-a-1.example vs acme.com"], txn=txn)
    assert "Held facts for pilot-a-1.example: none yet" in block
    assert len(block) <= GROUNDING_MAX_CHARS + 40 and block.endswith("(further held facts omitted)")


def test_system_prompt_forbids_general_knowledge_and_bounds_length() -> None:
    prompt = grounded_system_prompt("CTX", 2048)
    assert prompt.endswith("WORKSPACE DATA:\nCTX")
    assert "Never state company facts" in prompt and "only after the user" in prompt
    assert "under about 1126 words" in prompt
    assert "No workspace data was loaded" in grounded_system_prompt("", 2048)
