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

from account_intelligence.replica import answers as A
from account_intelligence.replica.model_answer import stream_with_model, take


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
async def test_an_invented_citation_or_a_list_stops_the_stream() -> None:
    invented = await _collect(["Acme is hiring [R1]. ", "Also [R9] says more. ", "End."])
    assert [k for k, _ in invented] == ["piece", "reset"]  # nothing after the bad sentence
    listed = await _collect(["- Acme is hiring [R1].\n\nMore [R2]."])
    assert [k for k, _ in listed] == ["reset"]


def test_pieces_wait_for_the_citations_after_a_sentence() -> None:
    assert take("Acme is hiring. ") == (None, False, "Acme is hiring. ")
    assert take("Acme is hiring. [R1] Next") == ("Acme is hiring. [R1] ", False, "Next")
    assert take("Last one", final=True) == ("Last one", True, "")


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
