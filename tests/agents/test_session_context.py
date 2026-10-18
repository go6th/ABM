"""Session-environment reference loading and ABM prompt injection contracts."""

from pathlib import Path

import pytest

from agents.session_context import PROMPT_INSTRUCTION, session_environment_context


def _write_reference(config_dir: Path, text: str) -> Path:
    path = config_dir / "skill" / "archive" / "references" / "session-environment.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_abm_prompt_gets_full_reference_unchanged_and_first(tmp_path: Path) -> None:
    source = (
        "# ABM Account Research: Session Environment and Methods\n\n"
        "## 4. Standing user rules injected on claude.ai\nverbatim rules\n"
        "## 5. Answering questions against an existing workspace\ncompute from files\n"
        "## 6. Parity checklist for any new deployment\ncheck all ten\n"
    )
    _write_reference(tmp_path, source)

    context = session_environment_context("abm_setup", tmp_path)

    assert context == f"{PROMPT_INSTRUCTION}\n\n{source}"
    assert context.index(PROMPT_INSTRUCTION) < context.index(source)
    assert "verbatim rules" in context


def test_non_abm_prompt_does_not_load_the_reference(tmp_path: Path) -> None:
    assert session_environment_context("chat_answer", tmp_path) == ""


def test_abm_prompt_fails_closed_for_missing_or_incomplete_reference(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="unavailable"):
        session_environment_context("abm_setup", tmp_path)

    _write_reference(tmp_path, "## 4. Standing user rules injected on claude.ai\n")
    with pytest.raises(RuntimeError, match="incomplete"):
        session_environment_context("abm_setup", tmp_path)
