"""Standing rules enforced as tests: never send, no live network paths, no secrets, plain docs."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
APP_FILES = sorted((REPO / "app").rglob("*.py"))

# Modules that could send email, chat, SMS or messages (roadmap s3: never send outreach).
OUTREACH_MODULES = {
    "smtplib",
    "imaplib",
    "poplib",
    "email",
    "sendgrid",
    "mailgun",
    "mailchimp_transactional",
    "postmarker",
    "twilio",
    "slack_sdk",
    "slack",
    "discord",
    "telegram",
    "boto3",
    "aiosmtplib",
}
# Phase 0 has no live broker or model client; network libraries arrive with Phase 1 adapters.
NETWORK_MODULES = {"requests", "httpx", "aiohttp", "urllib3", "anthropic", "socket", "http"}
OUTREACH_NAMES = re.compile(r"send_(email|mail|message|sms|outreach|linkedin|inmail)", re.I)
SECRET_PATTERNS = re.compile(r"(sk-ant-[A-Za-z0-9]|AKIA[0-9A-Z]{16}|xox[bp]-[0-9])")

NEW_DOCS = [
    "docs/decision_log.md",
    "docs/data_handling.md",
    "docs/phase1_backlog.md",
    "docs/PHASE0_STATUS.md",
    "docs/AUDIT.md",
    "docs/data_layer/PLAN.md",
    "docs/data_layer/CONVENTIONS.md",
    "tests/evals/golden/README.md",
    "infra/README.md",
    ".claude/skills/account-intelligence-builder/SKILL.md",
]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def test_app_files_found() -> None:
    assert len(APP_FILES) > 10


@pytest.mark.parametrize("path", APP_FILES, ids=lambda p: str(p.relative_to(REPO)))
def test_no_outreach_or_network_imports(path: Path) -> None:
    imported = _imports(path)
    assert not imported & OUTREACH_MODULES, f"outreach-capable import in {path}"
    assert not imported & NETWORK_MODULES, f"network client import in {path}"


@pytest.mark.parametrize("path", APP_FILES, ids=lambda p: str(p.relative_to(REPO)))
def test_no_outreach_functions_or_secrets(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    defined = [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    ]
    assert not [d for d in defined if OUTREACH_NAMES.search(d)]
    assert not SECRET_PATTERNS.search(text)


def test_config_holds_no_secrets() -> None:
    for path in (REPO / "config").glob("*.yaml"):
        assert not SECRET_PATTERNS.search(path.read_text(encoding="utf-8")), path


@pytest.mark.parametrize("rel", NEW_DOCS)
def test_new_docs_have_no_em_or_en_dashes(rel: str) -> None:
    text = (REPO / rel).read_text(encoding="utf-8")
    assert "\u2014" not in text, f"em dash in {rel}"
    assert "\u2013" not in text, f"en dash in {rel}"


def test_config_text_has_no_em_dashes() -> None:
    for path in (REPO / "config").glob("*.yaml"):
        assert "\u2014" not in path.read_text(encoding="utf-8"), path


def test_broker_names_stay_in_internal_places() -> None:
    """Broker names appear in app code only where the module is marked INTERNAL or is config."""
    names = re.compile(r"bitscale|sumble|predictleads|firecrawl", re.I)
    allowed = {"people_source.py", "settings.py"}
    for path in APP_FILES:
        text = path.read_text(encoding="utf-8")
        if names.search(text):
            assert path.name in allowed, f"broker name in {path}"
            if path.name == "people_source.py":
                assert "INTERNAL" in text
