"""Wiring and the workflow stage (spec 8): the Node renderer is NotConfigured until ported, so a
production build ends PARTIAL ``builders_not_configured``; the interim renderer is used only when
chosen; the build stage maps each builder outcome to its reference and never to "built" on
failure."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.platform.hermetic import MemoryPayloadStore
from account_intelligence.workflow.stages.core import build as build_stage
from exports.builder import BuildFailed
from exports.errors import ConfigError, NotConfigured
from exports.models import BuildResult
from exports.verify import LibreOffice
from exports.wiring import build_export_builder, renderer_command
from exports.word.renderer import SubprocessWordRenderer
from tests.fakes.exports import FixedClock

pytestmark = pytest.mark.asyncio


async def test_renderer_choices() -> None:
    assert renderer_command("node") is None  # the legacy library is not ported yet
    interim = renderer_command("python-interim")
    assert interim is not None and interim.argv[-1] == "exports.word.render_cli"
    with pytest.raises(ConfigError):
        renderer_command("word.exe")


async def test_production_default_is_not_configured_until_the_port() -> None:
    builder = build_export_builder(MemoryPayloadStore(), FixedClock(), {}, "node")
    assert builder.renderer is None and builder.pdf is None
    result = await builder.build_all(str(uuid4()), str(uuid4()))
    assert (result.status, result.reason) == ("not_configured", "builders_not_configured")
    assert result.errors == ("builders_not_configured: renderer",)


async def test_interim_renderer_and_render_checks_are_explicit_choices() -> None:
    env = {"EXPORT_RENDERER": "python-interim", "EXPORT_RENDER_CHECKS": "on"}
    builder = build_export_builder(MemoryPayloadStore(), FixedClock(), env, "node")
    assert isinstance(builder.renderer, SubprocessWordRenderer)
    assert isinstance(builder.pdf, LibreOffice) and builder.recalc is builder.pdf


class _Builder:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome

    async def build(self, tenant_id: str, run_id: str) -> list[str]:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return list(self.outcome)


def _deps(builder: Any) -> SimpleNamespace:
    return SimpleNamespace(builder=builder, tenant_id=uuid4(), run_id=uuid4())


@pytest.mark.parametrize(
    ("outcome", "reference", "exit_"),
    [
        (["d1", "d2", "d3", "d4", "d5", "d6"], "built", "converged"),
        (NotConfigured("no renderer"), "builders_not_configured", "no_progress"),
        (
            BuildFailed(
                BuildResult(
                    status="failed",
                    reason="build_failed",
                    errors=("leak: summary: forbidden content",),
                )
            ),
            "build_failed",
            "no_progress",
        ),
    ],
)
async def test_build_stage_maps_each_outcome(outcome: Any, reference: str, exit_: str) -> None:
    out = await build_stage(_deps(_Builder(outcome)), {})  # type: ignore[arg-type]
    assert out["references"]["build"] == reference
    assert out["loop_exits"]["build"] == exit_
    if reference == "built":
        assert out["counters"]["files_built"] == 6


async def test_build_stage_without_a_builder() -> None:
    out = await build_stage(_deps(None), {})  # type: ignore[arg-type]
    assert out["references"]["build"] == "builders_not_configured"
