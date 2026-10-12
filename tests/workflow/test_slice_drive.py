"""The slice driver: a cancel stops a long node within seconds; a slot wait is queueing."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from account_intelligence.workflow import runner
from account_intelligence.workflow.transitions import RunStatus


class _LongNode:
    """A graph whose one node runs until it is cancelled (a pull calling providers)."""

    def __init__(self) -> None:
        self.cancelled = False

    async def astream(self, *_: Any, **__: Any) -> Any:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        yield {}


@pytest.mark.asyncio
async def test_a_cancel_stops_a_long_node_without_waiting_for_it(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    statuses = iter([RunStatus.RUNNING, RunStatus.CANCELED])

    async def status(_: Any) -> RunStatus:
        return next(statuses)

    monkeypatch.setattr(runner, "_status", status)
    monkeypatch.setattr(runner, "CANCEL_POLL_S", 0.01)
    graph = _LongNode()
    assert await asyncio.wait_for(runner._drive(graph, None, {}, None), 5) is True  # type: ignore[arg-type]
    assert graph.cancelled  # the node stopped: no further provider calls


@pytest.mark.asyncio
async def test_a_finished_graph_is_not_a_cancel(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class Done:
        async def astream(self, *_: Any, **__: Any) -> Any:
            yield {}

    async def status(_: Any) -> RunStatus:
        return RunStatus.RUNNING

    monkeypatch.setattr(runner, "_status", status)
    assert await runner._drive(Done(), None, {}, None) is False  # type: ignore[arg-type]
