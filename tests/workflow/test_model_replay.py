"""A model answer is replayed within its run: a node re-run after a slice timeout pays once."""

from __future__ import annotations

from uuid import uuid4

import pytest

from account_intelligence.platform.hermetic import MemoryPayloadStore, ScriptedLlm
from account_intelligence.workflow.deps import RunDeps
from account_intelligence.workflow.spec import load_spec


def _deps(llm: ScriptedLlm, payloads: MemoryPayloadStore) -> RunDeps:
    return RunDeps(
        uuid4(), uuid4(), uuid4(), "acme.example", None, llm, payloads, None, load_spec()
    )  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_same_request_in_the_same_run_is_answered_once() -> None:
    llm = ScriptedLlm(lambda tier, system, user: f'{{"n": {len(user)}}}')
    deps = _deps(llm, MemoryPayloadStore())
    first = await deps.model("abm_pass2_extract", "sys", "payload one")
    again = await deps.model("abm_pass2_extract", "sys", "payload one")
    other = await deps.model("abm_pass2_extract", "sys", "payload two!")
    assert again.text == first.text and again.usage.input_tokens == 0
    assert other.text != first.text
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_a_task_kind_marked_not_cacheable_is_always_asked() -> None:
    llm = ScriptedLlm(lambda *a: "{}")
    deps = _deps(llm, MemoryPayloadStore())
    await deps.model("source_reread", "sys", "same")
    await deps.model("source_reread", "sys", "same")
    assert len(llm.calls) == 2


@pytest.mark.asyncio
async def test_a_dropped_model_connection_is_retryable_for_the_node_policy() -> None:
    import anthropic
    import httpx

    from account_intelligence.connectors.errors import RetryableProviderError

    class Dropping:
        async def complete(self, tier: str, system: str, user: str, *, max_tokens: int) -> None:
            raise anthropic.APIConnectionError(request=httpx.Request("POST", "https://x.invalid"))

    deps = _deps(Dropping(), MemoryPayloadStore())  # type: ignore[arg-type]
    # typed like the router's adapter, so the graph node RetryPolicy retries it (W6)
    with pytest.raises(RetryableProviderError):
        await deps.model("abm_pass1_extract", "sys", "payload")
