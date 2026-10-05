"""Shared builders for the job pipeline tests (synthetic corpus, production config)."""

from __future__ import annotations

from typing import Any

import pytest

from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.errors import HostileContent
from account_intelligence.grading.spans import check_hostile
from jobs.config import JobsConfig, load_jobs_config
from jobs.pipeline import JobsPipeline
from tests.fakes.jobs import Corpus, FakeLlm

CANARY = "CANARY-7f3a9e"


def is_hostile(text: str) -> bool:
    """The grading layer's one hostile-content check (J12 reuses G14)."""
    try:
        check_hostile(text, load_grading_config())
    except HostileContent:
        return True
    return False


def markers() -> tuple[str, ...]:
    return tuple(load_grading_config().spans.instruction_markers)


def build(
    corpus: Corpus,
    *,
    cfg: JobsConfig | None = None,
    extractor: FakeLlm | None = None,
    judge: FakeLlm | None = None,
    **source_kw: Any,
) -> tuple[JobsPipeline, Any, Any, FakeLlm, FakeLlm]:
    source, pages = corpus.source(**source_kw), corpus.pages()
    first, second = extractor or corpus.extractor(), judge or corpus.judge()
    pipeline = JobsPipeline(
        cfg or load_jobs_config(),
        source,
        pages,
        first,
        second,
        canary=CANARY,
        hostile_markers=markers(),
        is_hostile=is_hostile,
    )
    return pipeline, source, pages, first, second


@pytest.fixture
def corpus() -> Corpus:
    return Corpus()
