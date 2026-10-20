"""Ask streams its answer sentence by sentence and every streamed citation is a checked row
(SYNTHETIC rows and a fake model).

GUARANTEE(test_a_streamed_answer_only_sends_checked_citations)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.api.routes.ask import _mark_local_fallback
from account_intelligence.replica import answers as A
from account_intelligence.replica.model_answer import stream_with_model, take
from agents.skill_context import ask_skill_context


@dataclass
class _Event:
    kind: str
    text: str = ""
    usage: Any = None


class FakeProvider:
    """Streams a reply in awkward chunks (citations split across them)."""

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks

    async def stream(self, *_a: Any, **_k: Any):  # type: ignore[no-untyped-def]
        for c in self.chunks:
            yield _Event("text", c)
        yield _Event("usage", usage=None)


def _world() -> A.Workspace:
    acct = A.Acct(
        id=uuid4(),
        domain="acme.example",
        name="Acme",
        lanes=[],
        cadence=None,
        last_refresh=None,
        refreshes=1,
    )
    now = datetime.now(UTC)
    facts = [
        A.Row(f"fact:{uuid4()}", "fact", acct, t, "Acme news", "Acme", now, None, "confirmed")
        for t in ("Acme is hiring Salesforce developers", "Acme builds a data platform")
    ]
    return A.Workspace([acct], facts, [], [], [], [], [], {}, False)


async def _collect(chunks: list[str]) -> list[tuple[str, Any]]:
    ws = _world()
    q, local = A.draft_local("What is Acme hiring for?", ws, datetime.now(UTC))
    out = []
    async for kind, value in stream_with_model(
        FakeProvider(chunks), object(), q, ws, local, user_id="u", request_id="r"
    ):
        out.append((kind, value))
    return out


@pytest.mark.asyncio
async def test_a_streamed_answer_only_sends_checked_citations() -> None:
    events = await _collect(
        ["Acme is hiring [R", "1]. It also ", "builds a platform [R2].\n", "\nAsk about it [R1]."]
    )
    pieces = [v for k, v in events if k == "piece"]
    assert [p for p, _ in pieces] == [0, 0, 1]
    assert all("[R" not in text and "[[fact:" in text for _, text in pieces)
    kind, reply = events[-1]
    assert kind == "done" and len(reply.draft.paragraphs) == 2
    numbering: dict[str, int] = {}
    streamed = [A.number_marks(text, numbering) for _, text in pieces]
    final = A.finish(
        reply.draft, A.understand("q", _world(), None), _world(), datetime.now(UTC), None
    )
    # the numbers the stream showed are the numbers of the final answer
    assert [x["v"] for parts in streamed for x in parts if x["t"] == "cite"] == [
        x["v"] for para in final["paragraphs"] for x in para if x["t"] == "cite"
    ]


@pytest.mark.asyncio
async def test_every_answer_carries_the_owners_skill_in_its_system_prompt() -> None:
    seen: dict[str, Any] = {}

    class Capture(FakeProvider):
        async def stream(self, *a: Any, **k: Any):  # type: ignore[no-untyped-def]
            seen["system"] = k["system"]
            async for event in super().stream(*a, **k):
                yield event

    ws = _world()
    q, local = A.draft_local("What is Acme hiring for?", ws, datetime.now(UTC))
    async for _ in stream_with_model(
        Capture(["Acme is hiring [R1]."]), object(), q, ws, local, user_id="u", request_id="r"
    ):
        pass
    assert seen["system"].endswith(ask_skill_context())
    assert "## 5. Answering questions against an existing workspace" in seen["system"]


@pytest.mark.asyncio
async def test_an_invented_citation_is_dropped_but_a_list_or_table_streams() -> None:
    invented = await _collect(["Acme is hiring [R1]. ", "Also [R9] says more. ", "End."])
    # the sentence resting only on a row that was not sent is dropped, never streamed, and
    # the paid reply is kept (token review item 6, 2026-10-20: was a reset to the local answer)
    assert [k for k, _ in invented] == ["piece", "piece", "done"]
    assert all("R9" not in text and "says more" not in text for _, (_, text) in invented[:2])
    assert "says more" not in invented[-1][1].draft.paragraphs[0]
    # Markdown structure is kept line for line (owner decision 2026-10-17)
    listed = await _collect(["- Acme is hiring [R1].\n- It builds [R2].\n\nMore [R2]."])
    # each list line is its own checked piece (a sentence end), in the same paragraph
    assert [k for k, _ in listed] == ["piece", "piece", "piece", "done"]
    assert [p for p, _ in listed[:3] if True] and [v[0] for k, v in listed[:3]] == [0, 0, 1]
    kind, reply = listed[-1]
    assert kind == "done"
    assert reply.draft.paragraphs[0].startswith("- Acme is hiring")
    assert "\n- It builds" in reply.draft.paragraphs[0]
    table = await _collect(
        ["| Role | Seen |\n|---|---|\n| Architect | Jun [R1] |\n| Lead | Jul [R2] |"]
    )
    assert [k for k, _ in table] == ["piece", "done"]
    assert table[-1][1].draft.paragraphs[0].count("\n") == 3


def test_pieces_wait_for_the_citations_after_a_sentence() -> None:
    assert take("Acme is hiring. ") == (None, False, "Acme is hiring. ")
    assert take("Acme is hiring. [R1] Next") == ("Acme is hiring. [R1] ", False, "Next")
    assert take("Last one", final=True) == ("Last one", True, "")


def test_model_failure_keeps_local_draft_and_adds_a_fallback_notice() -> None:
    draft = A.Draft()
    draft.para("Acme is hiring Salesforce developers [[fact:1]].")

    _mark_local_fallback(draft)

    assert draft.paragraphs == [
        "AI summary unavailable. Showing workspace records:",
        "Acme is hiring Salesforce developers [[fact:1]].",
    ]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_route_streams_text_that_matches_the_final_answer(world: dict[str, Any]) -> None:
    from tests.replica.conftest import _call, _plane

    plane = _plane(world["a"], "priya-test", world["payloads"])
    r = await _call(
        plane,
        "POST",
        "/v1/chat/ask",
        json={"request_id": str(uuid4()), "question": "Who are the people at Highmark?"},
    )
    frames = []
    for raw in r.text.strip().split("\n\n"):
        lines = raw.split("\n")
        frames.append(
            (lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: ")))
        )
    kinds = [k for k, _ in frames]
    assert kinds.index("text") < kinds.index("answer")
    streamed: dict[int, list[dict[str, str]]] = {}
    for k, data in frames:
        if k == "text":
            streamed.setdefault(data["p"], []).extend(data["parts"])
    answer = next(d for k, d in frames if k == "answer")

    def flat(parts: list[dict[str, str]]) -> str:
        return "".join(f"[{p['v']}]" if p["t"] == "cite" else p["v"] for p in parts).strip()

    assert [flat(streamed[i]) for i in sorted(streamed)] == [flat(p) for p in answer["paragraphs"]]


@pytest.mark.asyncio
async def test_a_silent_ask_stream_sends_keepalives_and_keeps_every_frame() -> None:
    import asyncio

    from account_intelligence.api.routes.ask import KEEPALIVE, _with_keepalive

    async def source():  # type: ignore[no-untyped-def]
        yield b"event: status\ndata: {}\n\n"
        await asyncio.sleep(0.05)  # the model is writing: nothing is sent
        yield b"event: answer\ndata: {}\n\n"

    got = [f async for f in _with_keepalive(source(), every=0.01)]
    assert got[0] == b"event: status\ndata: {}\n\n" and got[-1] == b"event: answer\ndata: {}\n\n"
    assert KEEPALIVE in got[1:-1]
    # a comment frame carries no data line, so an SSE reader skips it
    assert KEEPALIVE.startswith(b":") and b"data:" not in KEEPALIVE

    async def broken():  # type: ignore[no-untyped-def]
        yield b"event: status\ndata: {}\n\n"
        raise RuntimeError("source failed")

    with pytest.raises(RuntimeError, match="source failed"):
        [f async for f in _with_keepalive(broken(), every=0.01)]
