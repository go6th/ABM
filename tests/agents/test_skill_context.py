"""ABM prompt-context coverage: task-specific skill context, the verbatim companion appendix,
archive completeness and fail-closed loading."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
import yaml

from account_intelligence.connectors.registry_internal import vendor_names
from account_intelligence.connectors.settings import load_providers_config
from agents.naming import client_hits
from agents.skill_context import (
    ARCHIVE_DIR,
    ASK_DIRECTIVE,
    ASK_SECTIONS,
    ASSEMBLED_PROMPT_MAX_CHARS,
    MAX_CHARS,
    SECTIONS,
    SESSION_ENVIRONMENT_DIRECTIVE,
    ask_skill_context,
    section,
    session_environment_context,
    skill_context,
    strip_tool_names,
    with_skill_context,
)

TASKS = sorted(SECTIONS)
REQUIRED_REFERENCES = (
    "checklist.md",
    "lessons.md",
    "signals.md",
    "tools.md",
    "output-spec.md",
    "phase1-pull.md",
    "phase2-build.md",
)
REQUIRED_SCRIPTS = ("rowcheck.py", "doc_checks.py", "third_party_scan.py")
REQUIRED_ASSETS = (
    "docx_lib.js",
    "people_workbook_example.py",
    "exec_deck_example.js",
    "jobs_workbook_example.py",
    "platform_ranking.py",
    "strip_tool_names.py",
)
REQUIRED_GATE_SCRIPTS = (
    "scripts/rowcheck.py",
    "scripts/doc_checks.py",
    "assets/strip_tool_names.py",
    "scripts/third_party_scan.py",
)
INSTALLED_VERSION_FILE = "references/checklist.md"


def companion_vendor_roster() -> tuple[str, ...]:
    text = session_environment_context()
    roster = text.split("### 3.1 What is connected on claude.ai\n", 1)[1].split("\n### 3.2", 1)[0]
    return tuple(re.findall(r"(?m)^- ([^:]+):", roster))


def test_the_archive_is_shipped_with_the_config() -> None:
    required = [
        "SKILL.md",
        *(f"references/{name}" for name in REQUIRED_REFERENCES),
        "references/session-environment.md",
        *(f"scripts/{name}" for name in REQUIRED_SCRIPTS),
        *(f"assets/{name}" for name in REQUIRED_ASSETS),
        *REQUIRED_GATE_SCRIPTS,
    ]
    missing = sorted({name for name in required if not (ARCHIVE_DIR / name).is_file()})
    assert not missing, f"ABM archive is incomplete; missing: {', '.join(missing)}"
    assert len(REQUIRED_REFERENCES) == 7
    assert len(REQUIRED_SCRIPTS) == 3
    assert len(REQUIRED_ASSETS) == 6
    assert len(REQUIRED_GATE_SCRIPTS) == 4


def _source_skill_version() -> str:
    source = yaml.safe_load((ARCHIVE_DIR.parent / "source.yaml").read_text(encoding="utf-8"))
    return str(source["skill"]["version"]["value"])


def _installed_skill_version() -> str:
    text = (ARCHIVE_DIR / INSTALLED_VERSION_FILE).read_text(encoding="utf-8")
    match = re.search(r"(?im)^Version\s+(\d+\.\d+)\b", text)
    assert match, f"Installed skill version marker not found in {INSTALLED_VERSION_FILE}"
    return match.group(1)


def test_installed_skill_version_matches_source_yaml() -> None:
    source_version = _source_skill_version()
    installed_version = _installed_skill_version()
    assert source_version == installed_version, (
        f"installed ABM skill version is {installed_version} (from {INSTALLED_VERSION_FILE}); "
        f"config/skill/source.yaml declares {source_version}"
    )


@pytest.mark.xfail(reason="v1.12 package not available", strict=True)
def test_v1_12_package_not_available() -> None:
    """Track the requested v1.12 package as unavailable without relabeling v1.11."""
    assert _installed_skill_version() == "1.12", "v1.12 package not available"


def test_skill_md_marker_agrees_with_source_version_when_present() -> None:
    skill_text = (ARCHIVE_DIR / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"(?im)^\s*(?:version\s*:\s*|.*?\bv)(1\.\d+(?:\.\d+)?)\b", skill_text)
    if match:
        skill_version = match.group(1)
        assert skill_version == _source_skill_version(), (
            f"SKILL.md declares version {skill_version}, but config/skill/source.yaml declares "
            f"{_source_skill_version()}"
        )


def test_session_environment_archive_matches_source_sha256() -> None:
    source = Path("/home/sakib/Downloads/ABM_Session_Environment_and_Methods (1).md")
    archived = ARCHIVE_DIR / "references" / "session-environment.md"
    assert source.is_file(), f"source companion document is missing: {source}"
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    archive_hash = hashlib.sha256(archived.read_bytes()).hexdigest()
    assert archive_hash == source_hash, (
        f"session-environment.md drift: source sha256={source_hash}, archive sha256={archive_hash}"
    )


@pytest.mark.parametrize("task", TASKS)
def test_every_task_kind_gets_its_context_without_tool_names(task: str) -> None:
    text = skill_context(task)
    assert text and len(text) <= MAX_CHARS
    assert "Rules that matter most" in text
    assert client_hits(text) == (), [text[max(0, o - 30) : o + 30] for _, o in client_hits(text)]
    for name in vendor_names(load_providers_config()).values():
        if name != "Parallel":  # an English word; its provider term is what the scan catches
            assert name.lower() not in text.lower(), name


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
    with pytest.raises(FileNotFoundError, match="required ABM skill file is missing"):
        skill_context("abm_distill", Path("/nonexistent"))


def test_documented_skill_path_maps_to_the_installed_archive() -> None:
    prompt = with_skill_context("PROMPT", "abm_distill")
    assert (
        "/home/claude/abm-account-research/ maps to the bundled runtime directory "
        "config/skill/archive/" in prompt
    )
    assert "resolve relative references/, assets/ and scripts/ paths from that directory" in prompt


@pytest.mark.parametrize("task", TASKS)
def test_every_abm_prompt_receives_the_companion_document_verbatim_and_in_full(task: str) -> None:
    appendix = session_environment_context()
    prompt = with_skill_context("PROMPT", task)
    block = f"<!-- references/session-environment.md, verbatim -->\n{appendix}"
    rules_start = appendix.index("## 4.")
    rules_end = appendix.index("\n## 5.", rules_start)

    assert prompt.index(SESSION_ENVIRONMENT_DIRECTIVE) < prompt.index(block)
    assert block in prompt
    assert appendix[rules_start:rules_end] in prompt
    assert appendix.splitlines()[0] in prompt
    assert appendix.splitlines()[-1] in prompt
    assert prompt.index(block) < prompt.index("The owner's research skill")


@pytest.mark.parametrize("task", TASKS)
def test_full_prompt_records_companion_vendor_roster(task: str) -> None:
    prompt = with_skill_context("PROMPT", task)
    roster_names = companion_vendor_roster()
    appeared = tuple(
        name
        for name in roster_names
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", prompt, re.I)
    )
    assert appeared == roster_names


@pytest.mark.parametrize("task", TASKS)
def test_full_prompt_stays_under_explicit_size_limit(task: str) -> None:
    prompt = with_skill_context("PROMPT", task)
    assert len(prompt) <= ASSEMBLED_PROMPT_MAX_CHARS


def test_session_context_fails_closed_instead_of_truncating_or_silently_skipping() -> None:
    with pytest.raises(FileNotFoundError, match="required ABM context is missing"):
        session_environment_context(Path("/nonexistent"))


def test_sections_and_stripping_are_exact() -> None:
    md = "# T\n\n## One\n\na\n\n## Two\n\nb\n"
    assert section(md, "One") == "## One\na"
    assert section(md, "Two") == "## Two\nb"
    assert section(md, "Three") == ""
    assert section(md, None) == md.strip()
    first, second, *_ = sorted(vendor_names(load_providers_config()).values())
    assert strip_tool_names(f"Use {first} and {second}, {first}.") == "Use 6th agents."
    assert strip_tool_names(f"{first}ed") == f"{first}ed"  # whole words only
    assert strip_tool_names("in parallel") == "in parallel"
    assert strip_tool_names("parallel processing") == "parallel processing"
    assert strip_tool_names(f"{first}ing") == f"{first}ing"  # matching is whole word
    assert strip_tool_names(f"{first} and {first}") == "6th agents"


def test_ask_reads_the_skill_rules_and_the_companion_answering_rules() -> None:
    text = ask_skill_context()
    assert text.startswith(ASK_DIRECTIVE) and len(text) <= MAX_CHARS
    for file, heading in ASK_SECTIONS:
        assert f"<!-- {file} -->\n## {heading}" in text, (file, heading)
    # the workspace answering rules that keep Ask consistent with claude.ai
    assert "When a strict query returns zero results" in text
    assert "Drop lists (false positive exclusions) apply to every file" in text
    # the research-run sandbox and the tool roster stay out of Ask
    assert "### 3.1 What is connected on claude.ai" not in text
    assert client_hits(text) == (), [text[max(0, o - 30) : o + 30] for _, o in client_hits(text)]
    for name in vendor_names(load_providers_config()).values():
        if name != "Parallel":  # an English word; its provider term is what the scan catches
            assert name.lower() not in text.lower(), name


def test_ask_context_fails_closed_on_a_missing_file_or_section(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="required ABM skill file is missing"):
        ask_skill_context(Path("/nonexistent"))
    for file, _ in ASK_SECTIONS:
        (tmp_path / file).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / file).write_text("# empty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="required ABM skill section is missing"):
        ask_skill_context(tmp_path)
