"""The parity bundle beside the export build never decides the run (merge review 2026-10-30):
a port that raises, returns a skip or hangs past its budget leaves the run's outcome, files and
exits exactly as they are without a port; the counters record what happened."""

from __future__ import annotations

from typing import Any

import pytest

from agents.production.parity_build import ParityBuildPort, parity_port
from tests.agents import worlds


class _Port:
    def __init__(self, answer: Any = None, error: BaseException | None = None) -> None:
        self.answer, self.error, self.calls = answer, error, 0

    async def build(self, ctx: Any) -> dict[str, Any]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.answer


async def _baseline(make: Any) -> tuple[Any, Any]:
    h = make(worlds.rich())
    h.grant_consent()
    return await h.run("without")


@pytest.mark.asyncio
async def test_a_parity_build_that_raises_leaves_the_run_unchanged(make: Any) -> None:
    record, base = await _baseline(make)
    port = _Port(error=RuntimeError("synthetic renderer crash"))
    h = make(worlds.rich(), parity=port)
    h.grant_consent()
    _, state = await h.run("with-a-crash")
    assert port.calls == 1
    for key in ("outcome", "error_code", "client_files", "internal_files", "exits"):
        assert state.get(key) == base.get(key), key
    assert state["counters"]["parity_skipped"] == 1 and state["counters"]["parity_built"] == 0


@pytest.mark.asyncio
async def test_a_parity_build_that_is_skipped_is_not_a_failure(make: Any) -> None:
    _, base = await _baseline(make)
    port = _Port({"skipped": "missing: libreoffice"})
    h = make(worlds.rich(), parity=port)
    h.grant_consent()
    _, state = await h.run("skipped")
    assert state["outcome"] == base["outcome"]
    assert state["counters"]["parity_skipped"] == 1


@pytest.mark.asyncio
async def test_a_built_version_is_recorded_in_the_counters_only(make: Any) -> None:
    _, base = await _baseline(make)
    port = _Port({"version": 4, "status": "built"})
    h = make(worlds.rich(), parity=port)
    h.grant_consent()
    _, state = await h.run("built")
    assert state["outcome"] == base["outcome"] and state["client_files"] == base["client_files"]
    assert state["counters"]["parity_version"] == 4 and state["counters"]["parity_built"] == 1


@pytest.mark.asyncio
async def test_a_failed_gate_version_is_counted_as_not_built(make: Any) -> None:
    port = _Port({"version": 2, "status": "failed"})
    h = make(worlds.rich(), parity=port)
    h.grant_consent()
    _, state = await h.run("failed-gate")
    assert state["counters"]["parity_version"] == 2 and state["counters"]["parity_built"] == 0
    assert state["outcome"] in ("completed", "partial")


def test_the_switch_is_off_unless_it_says_on() -> None:
    assert parity_port(object(), {}) is None  # type: ignore[arg-type]
    assert parity_port(object(), {"PARITY_BUNDLE": "off"}) is None  # type: ignore[arg-type]
    assert isinstance(parity_port(object(), {"PARITY_BUNDLE": "on"}), ParityBuildPort)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_a_parity_build_that_hangs_is_cut_at_its_ceiling(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from agents import graph

    class _Hangs:
        async def build(self, ctx: Any) -> dict[str, Any]:
            await asyncio.sleep(30)
            return {"version": 1, "status": "built"}

    monkeypatch.setattr(graph, "PARITY_CEILING_S", 0.05)
    _, base = await _baseline(make)
    h = make(worlds.rich(), parity=_Hangs())
    h.grant_consent()
    _, state = await h.run("hang")
    assert state["outcome"] == base["outcome"] and state["client_files"] == base["client_files"]
    assert state["counters"]["parity_skipped"] == 1
