"""Shared fixtures for the export builder tests: production configuration, synthetic corpus,
in-memory fakes. Database tests are marked ``requires_postgres`` (see tests/conftest.py)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from account_intelligence.grading.config import GradingConfig, load_grading_config
from exports.builder import ExportBuilder
from exports.config import ContentRules, ExportConfig, load_export_config
from exports.content import SnapshotContent
from exports.word.renderer import SubprocessWordRenderer, python_renderer
from tests.fakes.exports import FakeReader, InProcessRenderer, MemoryStore


@pytest.fixture(scope="session")
def cfg() -> ExportConfig:
    """The repository export configuration."""
    return load_export_config()


@pytest.fixture(scope="session")
def rules(cfg: ExportConfig) -> ContentRules:
    """The content rules."""
    return cfg.rules


@pytest.fixture(scope="session")
def grading() -> GradingConfig:
    """The repository grading configuration."""
    return load_grading_config()


@pytest.fixture
def make_builder(cfg: ExportConfig, grading: GradingConfig) -> Callable[..., ExportBuilder]:
    """A builder over fakes; ``subprocess=True`` uses the sandboxed renderer."""

    def make(
        content: SnapshotContent,
        store: MemoryStore | None = None,
        *,
        subprocess: bool = False,
        config: ExportConfig | None = None,
    ) -> ExportBuilder:
        c = config or cfg
        renderer = (
            SubprocessWordRenderer(python_renderer(), c.rules.renderer)
            if subprocess
            else InProcessRenderer()
        )
        return ExportBuilder(FakeReader(content), store or MemoryStore(), renderer, c, grading)

    return make
