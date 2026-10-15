"""The owner's research skill as model context (operator request 2026-10-16): every ABM task
kind gets the parts of the skill its step needs, no data or enrichment tool name reaches the
model (G9: it would echo them into client-bound output), the context is bounded, and a missing
archive leaves the agent prompt alone."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.naming import client_hits
from agents.skill_context import (
    ARCHIVE_DIR,
    MAX_CHARS,
    SECTIONS,
    section,
    skill_context,
    strip_tool_names,
    with_skill_context,
)

TASKS = sorted(SECTIONS)


def test_the_archive_is_shipped_with_the_config() -> None:
    assert (ARCHIVE_DIR / "SKILL.md").is_file()
    for file in ("lessons", "output-spec", "phase2-build", "signals", "checklist"):
        assert (ARCHIVE_DIR / "references" / f"{file}.md").is_file(), file


@pytest.mark.parametrize("task", TASKS)
def test_every_task_kind_gets_its_context_without_tool_names(task: str) -> None:
    text = skill_context(task)
    assert text and len(text) <= MAX_CHARS
    assert "Rules that matter most" in text
    assert client_hits(text) == (), [text[max(0, o - 30) : o + 30] for _, o in client_hits(text)]
    for name in ("BitScale", "Sumble", "Firecrawl", "PredictLeads"):
        assert name.lower() not in text.lower()


def test_each_step_reads_what_it_needs() -> None:
    assert "lessons.md" in skill_context("abm_pass2_extract")
    assert "signals.md" in skill_context("abm_pass1_extract")
    assert "phase2-build.md" in skill_context("abm_sense_check")
    assert "output-spec.md" in skill_context("abm_distill")
    assert "output-spec.md" not in skill_context("abm_pass1_extract")


def test_the_prompt_is_followed_by_the_context_or_stands_alone() -> None:
    assert with_skill_context("PROMPT", "abm_distill").startswith("PROMPT\n\n")
    assert "research skill" in with_skill_context("PROMPT", "abm_distill")
    assert with_skill_context("PROMPT", "not_a_task") == "PROMPT"
    assert skill_context("abm_distill", Path("/nonexistent")) == ""


def test_sections_and_stripping_are_exact() -> None:
    md = "# T\n\n## One\n\na\n\n## Two\n\nb\n"
    assert section(md, "One") == "## One\na"
    assert section(md, "Two") == "## Two\nb"
    assert section(md, "Three") == ""
    assert section(md, None) == md.strip()
    assert (
        strip_tool_names("Use Firecrawl and BitScale, Sumble.") == "Use 6th agents and 6th agents."
    )
    assert strip_tool_names("firecrawled") == "firecrawled"  # whole words only
