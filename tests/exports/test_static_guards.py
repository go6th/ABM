"""Static guards (spec 2.4, 2.5, B2, 11, 12): no style literal outside the theme loader; no vendor
or broker name in the builders or their configuration; no model, network, clock or randomness at
build time; pure modules do no I/O; every configuration entry names its source or status; the
generated documents are current; one implementation per rule."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

from account_intelligence.safety.client_output import load_patterns
from exports import docs

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src/exports"
SOURCES = sorted(p for p in SRC.rglob("*.py"))
CONFIGS = (ROOT / "config/export_theme.yaml", ROOT / "config/export_rules.yaml")


def _rel(p: Path) -> str:
    return str(p.relative_to(SRC))


# Style literals: hex colours, font faces, point/inch sizes with a number, width/size numbers.
STYLE = [
    re.compile(r"""["'][0-9A-Fa-f]{6}["']"""),
    re.compile(r"""["'](Calibri|Arial|Cambria|Times New Roman|Helvetica|Aptos)["']"""),
    re.compile(r"\b(Pt|Inches|Cm|Mm|Emu|Twips)\(\s*\d"),
    re.compile(r"\b(size|sz|width|height)\s*=\s*\d"),
]


@pytest.mark.parametrize("path", SOURCES, ids=_rel)
def test_no_style_literal_outside_the_theme_loader(path: Path) -> None:
    if path.name == "config.py":
        return
    text = path.read_text()
    for pattern in STYLE:
        found = pattern.findall(text)
        assert not found, f"{_rel(path)}: style literal {found[:3]} (use the theme)"


@pytest.mark.parametrize("path", [*SOURCES, *CONFIGS], ids=lambda p: p.name)
def test_no_vendor_or_broker_name(path: Path) -> None:
    text = path.read_text()
    for pattern in load_patterns():
        assert not pattern.search(text), f"{path.name}: matches /{pattern.pattern}/"


# B2: nothing that could reach a model, the network, the wall clock or randomness.
B2 = re.compile(
    r"datetime\.now|date\.today|time\.time\(|\bimport random\b|from random |uuid4|uuid1|"
    r"\bhttpx\b|\brequests\b|urllib\.request|\bsocket\b|\banthropic\b|LlmPort|vertex|"
    r"google\.cloud|os\.environ|getenv"
)


@pytest.mark.parametrize("path", SOURCES, ids=_rel)
def test_no_model_network_clock_or_randomness(path: Path) -> None:
    allowed = {"store.py": "clock.now"}  # the injected clock, for the record's built_at only
    text = path.read_text()
    found = B2.findall(text)
    assert not found, f"{_rel(path)}: {found}"
    if path.name in allowed:
        assert allowed[path.name] in text


PURE = (
    "policy.py",
    "numbers.py",
    "citations.py",
    "fingerprint.py",
    "change_note.py",
    "readers/common.py",
    "readers/summary.py",
    "readers/evidence.py",
    "readers/people.py",
    "readers/jobs.py",
    "readers/tech.py",
    "readers/tracker.py",
    "word/compose.py",
)
IO_MODULES = {
    "sqlalchemy",
    "subprocess",
    "os",
    "socket",
    "asyncio",
    "shutil",
    "tempfile",
    "pathlib",
    "httpx",
    "logging",
    "opentelemetry",
}  # io.BytesIO is in memory


@pytest.mark.parametrize("name", PURE)
def test_pure_modules_import_no_io(name: str) -> None:
    tree = ast.parse((SRC / name).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & IO_MODULES, f"{name} imports {imported & IO_MODULES}"


def _provenance_ok(node: object) -> bool:
    if not isinstance(node, dict):
        return False
    keys = set(node)
    return bool(
        keys & {"source", "status"} or any(k.endswith(("_source", "_status")) for k in keys)
    )


def test_every_config_entry_names_its_source_or_status() -> None:
    rules = yaml.safe_load(CONFIGS[1].read_text())
    assert rules["status"] and rules["decision"]
    for key, value in rules.items():
        if key in ("version", "status", "decision"):
            continue
        assert _provenance_ok(value), f"export_rules.yaml: {key} names no source or status"
    theme = yaml.safe_load(CONFIGS[0].read_text())
    assert theme["status"] in ("proposal", "sourced") and theme["source"] and theme["decision"]
    assert theme["word"]["page"]["source"]
    if theme["status"] == "proposal":
        assert "TODO" in theme["source"], "an unsourced look says so"


def test_no_phrase_list_is_invented() -> None:
    rules = yaml.safe_load(CONFIGS[1].read_text())
    for key, items in (("stale_phrases", "phrases"), ("abbreviations", "entries")):
        block = rules[key]
        assert block[items] == [] or "TODO" not in block["source"], key
        assert block[items] or "TODO" in block["source"], f"{key}: empty lists say TODO"


def test_generated_documents_are_current() -> None:
    assert docs.main(["--check"]) == 0


def test_docs_check_reports_staleness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docs, "ROOT", tmp_path)
    monkeypatch.setattr(docs, "THEME", tmp_path / "docs/exports/theme.md")
    monkeypatch.setattr(docs, "FILES", tmp_path / "docs/exports/files.md")
    assert docs.main(["--check"]) == 1
    assert docs.main([]) == 0 and docs.main(["--check"]) == 0


def test_one_implementation_per_rule() -> None:
    """The builders reuse the grading statement policy, the tracker client view, the people and
    jobs export selections and the client-output scanner; they never re-define them."""
    text = "\n".join(p.read_text() for p in SOURCES)
    for reused in (
        "grading.product import display_label, statement_policy",
        "tracker.render import render_client_view",
        "people.export import build_export",
        "jobs.export import is_exported",
        "safety.client_output import scan_text",
    ):
        assert reused in text, reused
    for name in (
        "statement_policy",
        "display_label",
        "render_client_view",
        "build_export",
        "is_exported",
        "scan_text",
        "tally",
    ):
        assert not re.search(rf"\bdef {name}\(", text), f"{name} is re-defined here"
