"""The project skills stay valid: frontmatter, cited paths, alignment checks, plain text."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
SKILLS = REPO / ".claude" / "skills"
ENHANCED = SKILLS / "account-intelligence"
PLATFORM = SKILLS / "account-intelligence-platform"
ALIGN = ENHANCED / "scripts" / "check_alignment.py"
TENANCY = ENHANCED / "scripts" / "check_tenancy.py"


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert match, f"{path} has no frontmatter"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize("skill", sorted(p.parent.name for p in SKILLS.glob("*/SKILL.md")))
def test_skill_frontmatter(skill: str) -> None:
    meta = _frontmatter(SKILLS / skill / "SKILL.md")
    assert meta["name"] == skill
    assert 20 < len(meta["description"]) <= 1024


def test_only_the_enhanced_skill_claims_to_be_the_entry_point() -> None:
    descriptions = {
        p.parent.name: _frontmatter(p)["description"] for p in SKILLS.glob("*/SKILL.md")
    }
    assert descriptions["account-intelligence"].startswith("Entry point")
    assert descriptions["account-intelligence-builder"].startswith("SUPERSEDED")
    assert (
        "Start from the account-intelligence skill" in descriptions["account-intelligence-platform"]
    )


_PATH = re.compile(r"`([A-Za-z0-9_./-]+/[A-Za-z0-9_.-]+\.(?:md|py|yaml|sh|sql|ini|txt|json))`")
_SKIP = ("~", "<", "$", "*", "http", "...")


def _cited_paths(text: str) -> set[str]:
    return {m for m in _PATH.findall(text) if not m.startswith(_SKIP)}


ENHANCED_DOCS = sorted(
    p.relative_to(REPO).as_posix() for p in ENHANCED.rglob("*") if p.suffix in {".md", ".py", ".sh"}
)


@pytest.mark.parametrize("rel", ENHANCED_DOCS)
def test_cited_paths_exist(rel: str) -> None:
    path = REPO / rel
    roots = (REPO, ENHANCED, PLATFORM, path.parent, PLATFORM / "references")
    missing = [
        cited
        for cited in _cited_paths(path.read_text(encoding="utf-8"))
        if not any((root / cited).exists() for root in roots)
    ]
    assert not missing, f"{rel} cites missing files: {missing}"


@pytest.mark.parametrize("rel", [*ENHANCED_DOCS, "docs/BUILD_LOG.md"])
def test_no_em_or_en_dashes(rel: str) -> None:
    text = (REPO / rel).read_text(encoding="utf-8")
    assert "\u2014" not in text and "\u2013" not in text, rel


def test_reconciliation_register_is_well_formed() -> None:
    text = (ENHANCED / "references" / "00-reconciliation.md").read_text(encoding="utf-8")
    rows = re.findall(r"^\| (R-\d{2}) \|.*\| \*\*(Settled|Proposed|Open)", text, re.M)
    ids = [r[0] for r in rows]
    assert ids == [f"R-{i:02d}" for i in range(1, len(ids) + 1)]
    assert len(ids) >= 15
    assert dict(rows)["R-01"] == "Open"


# --------------------------------------------------------------------- alignment script


def _align(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(ALIGN), *args], capture_output=True, text=True, check=False
    )


def test_repo_config_and_platform_assets_align() -> None:
    result = _align()
    assert result.returncode == 0, result.stdout
    assert "OK: 0 errors" in result.stdout


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    shutil.copytree(REPO / "config", tmp_path / "config")
    shutil.copytree(
        PLATFORM / "assets", tmp_path / ".claude/skills/account-intelligence-platform/assets"
    )
    return tmp_path


def _edit(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


@pytest.mark.parametrize(
    ("file", "old", "new", "message"),
    [
        ("config/checklist.yaml", "priority: Medium", "priority: High", "priority differs"),
        ("config/checklist.yaml", '"Podcasts"', '"Podcast appearances"', "label differs"),
        (
            "config/grading.yaml",
            "min_distinct_queries: 3",
            "min_distinct_queries: 4",
            "Lead verification",
        ),
        (
            ".claude/skills/account-intelligence-platform/assets/client_output_denylist.txt",
            "\\bsumble\\b",
            "# removed",
            "no pattern catching broker 'sumble'",
        ),
        (
            ".claude/skills/account-intelligence-platform/assets/grading.yaml",
            "may_state_to_prospect: false\n    becomes: question\n  hypothesis",
            "may_state_to_prospect: true\n    becomes: question\n  hypothesis",
            "only confirmed may be",
        ),
    ],
)
def test_alignment_catches_drift(
    fake_repo: Path, file: str, old: str, new: str, message: str
) -> None:
    _edit(fake_repo / file, old, new)
    result = _align("--repo", str(fake_repo))
    assert result.returncode == 1
    assert message in result.stdout


def test_alignment_strict_fails_on_notes() -> None:
    result = _align("--strict")
    assert result.returncode == 1
    assert "NOTE" in result.stdout


def test_tenancy_check_skips_without_a_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(TENANCY)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert result.stdout.startswith("SKIP")


def test_platform_skill_selftest() -> None:
    result = subprocess.run(  # noqa: S603
        ["bash", str(PLATFORM / "scripts" / "selftest.sh")],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "selftest OK" in result.stdout
