"""Rendering: the same look for every account (spec 9.4), byte-identical rebuilds and fixed
document properties (B2, B10), theme consistency, renderer hardening (spec 9.9), idempotent and
versioned storage (B14) and the log canary (spec 8.5)."""

from __future__ import annotations

import getpass
import io
import json
import logging
import shutil
import socket
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from lxml import etree

from account_intelligence.grading.config import load_grading_config
from exports.builder import ExportBuilder
from exports.config import ExportConfig, load_export_config
from exports.content import SnapshotContent
from exports.errors import NotConfigured, RenderError
from exports.fingerprint import docx_fingerprint, is_subset, xlsx_fingerprint
from exports.models import BuildResult
from exports.readers.common import MapContext
from exports.readers.people import map_people
from exports.readers.summary import map_summary
from exports.word import render_cli
from exports.word.compose import compose_summary
from exports.word.contract import DocumentSpec
from exports.word.renderer import (
    RendererCommand,
    SubprocessWordRenderer,
    clean_env,
    node_renderer,
    python_renderer,
)
from tests.exports.corpus import account, rich, sparse
from tests.fakes.exports import KilledMidBuild, MemoryStore

Make = Callable[..., ExportBuilder]
CONFIG = Path(__file__).resolve().parents[2] / "config"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


async def build(
    make: Make, content: SnapshotContent, **kw: Any
) -> tuple[BuildResult, dict[str, bytes]]:
    store = MemoryStore()
    result = await make(content, store, **kw).build_all(content.tenant_id, content.run_id)
    assert result.status == "complete", result.errors
    return result, {d.kind: store.objects[d.object_key] for d in result.deliverables}


def fingerprint(kind: str, body: bytes) -> dict[str, Any]:
    return docx_fingerprint(body) if kind in ("summary", "evidence") else xlsx_fingerprint(body)


# ---- the same look -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_accounts_have_identical_fingerprints(make_builder: Make) -> None:
    _, a = await build(make_builder, rich("Synthetic Rich Corp", "rich.example"))
    other = account(
        "Synthetic Beta Group",
        "beta.example",
        n_facts=9,
        n_people=7,
        n_postings=5,
        n_detections=3,
        long_text=True,
        hostile_text=True,
    )
    _, b = await build(make_builder, other)
    for kind in ("summary", "evidence", "people", "jobs", "tech"):
        assert fingerprint(kind, a[kind]) == fingerprint(kind, b[kind]), kind
        assert a[kind] != b[kind]  # the content differs


@pytest.mark.asyncio
async def test_an_account_with_empty_sections_uses_a_subset_of_the_look(make_builder: Make) -> None:
    _, full = await build(make_builder, rich())
    _, empty = await build(make_builder, sparse())
    for kind in ("summary", "evidence", "people", "jobs", "tech"):
        assert is_subset(fingerprint(kind, empty[kind]), fingerprint(kind, full[kind])), kind


def test_subset_rejects_a_different_look(cfg: ExportConfig) -> None:
    doc = {
        "kind": "docx",
        "page": {"pgSz": {"w": "1"}},
        "styles": "s",
        "rPr": ["a"],
        "pPr": [],
        "tblPr": [],
        "tblGrid": [],
        "trPr": [],
        "tcPr": [],
    }
    assert is_subset(doc, doc)
    assert not is_subset({**doc, "rPr": ["b"]}, doc)
    assert not is_subset({**doc, "page": {}}, doc)
    assert not is_subset(doc, {"kind": "xlsx"})
    book = {"kind": "xlsx", "styles": {"fonts": ["f"]}, "sheets": [{"name": "A"}]}
    assert is_subset(book, book)
    assert not is_subset({**book, "sheets": [{"name": "B"}]}, book)
    assert not is_subset({**book, "styles": {"fonts": ["g"]}}, book)


def _theme_dir(tmp: Path, font: str, color: str) -> Path:
    for name in ("export_rules.yaml", "lanes.yaml", "output_spec.yaml"):
        shutil.copy(CONFIG / name, tmp / name)
    theme = yaml.safe_load((CONFIG / "export_theme.yaml").read_text())
    for section in ("word", "excel"):
        for spec in theme[section]["fonts"].values():
            spec["name"] = font
            spec["color"] = color
    theme["word"]["page"]["margins_in"]["left"] = 1.25
    (tmp / "export_theme.yaml").write_text(yaml.safe_dump(theme))
    return tmp


@pytest.mark.asyncio
async def test_a_theme_change_changes_every_file_consistently(
    make_builder: Make, tmp_path: Path
) -> None:
    changed = load_export_config(_theme_dir(tmp_path, "Synthetic Serif", "123456"))
    base_cfg = load_export_config()
    assert changed.theme_hash != base_cfg.theme_hash
    _, old = await build(make_builder, account())
    result, new = await build(make_builder, account(), config=changed)
    assert all(d.theme_hash == changed.theme_hash for d in result.deliverables)
    for kind in ("summary", "evidence"):
        with zipfile.ZipFile(io.BytesIO(new[kind])) as z:
            for name in z.namelist():
                if name.startswith(("word/document", "word/header", "word/footer")):
                    root = etree.fromstring(z.read(name))
                    faces = {f.get(f"{W}ascii") for f in root.iter(f"{W}rFonts")}
                    colors = {c.get(f"{W}val") for c in root.iter(f"{W}color")}
                    assert faces <= {"Synthetic Serif"}, (kind, name, faces)
                    assert colors <= {"123456"}, (kind, name, colors)
        assert fingerprint(kind, old[kind])["page"] != fingerprint(kind, new[kind])["page"]
    for kind in ("people", "jobs", "tech"):
        with zipfile.ZipFile(io.BytesIO(new[kind])) as z:
            styles = etree.fromstring(z.read("xl/styles.xml"))
        ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        faces = {n.get("val") for n in styles.iter(f"{ns}name")}
        assert faces == {"Synthetic Serif"}, (kind, faces)


# ---- determinism and properties ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebuilds_are_byte_identical(make_builder: Make) -> None:
    content = rich()
    first, a = await build(make_builder, content)
    second, b = await build(make_builder, content, subprocess=True)
    assert a == b
    assert first.manifest == second.manifest
    assert first.manifest.snapshot_hash == content.snapshot_hash()


@pytest.mark.asyncio
async def test_document_properties_hold_only_the_attribution(make_builder: Make) -> None:
    _, files = await build(make_builder, account())
    # tool, user and machine names nowhere; vendor product names nowhere in the properties
    # (the Office theme part legitimately lists font faces such as "Microsoft Himalaya")
    forbidden = ("python-docx", "openpyxl", "LibreOffice", getpass.getuser(), socket.gethostname())
    vendor = ("Microsoft", "Macintosh", "Word", "Excel")
    for kind, body in files.items():
        members = [(kind, body)]
        if kind == "workspace":
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                members += [(n, z.read(n)) for n in z.namelist() if n.endswith((".docx", ".xlsx"))]
        for where, data in members:
            if where == "workspace":
                continue
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                core = z.read("docProps/core.xml").decode()
                app = z.read("docProps/app.xml").decode()
                assert "<dc:creator>6th agents</dc:creator>" in core
                assert "2026-09-30T00:00:00Z" in core
                assert "<Company>6th agents</Company>" in app and "Application" not in app
                assert not any(v in core + app for v in vendor)
                assert not any(
                    n.startswith(("customXml/", "docProps/thumbnail")) for n in z.namelist()
                )
                for name in z.namelist():
                    text = z.read(name).decode("utf-8", "ignore")
                    for word in forbidden:
                        assert word not in text, (where, name, word)


# ---- renderer hardening ------------------------------------------------------------------------


def _script(code: str) -> RendererCommand:
    return RendererCommand("test", (sys.executable, "-I", "-c", code))


def _limits(cfg: ExportConfig, **update: Any) -> Any:
    return cfg.rules.renderer.model_copy(update=update)


def _spec(cfg: ExportConfig) -> DocumentSpec:
    """The Summary spec of the base synthetic account."""
    ctx = MapContext(account(), cfg, load_grading_config())
    summary = map_summary(ctx, map_people(ctx).model).model
    assert summary is not None
    return compose_summary(summary, cfg, "Synthetic Alpha Ltd Account Summary")


def test_renderer_timeout_kills_and_reruns_once(cfg: ExportConfig) -> None:
    r = SubprocessWordRenderer(_script("import time; time.sleep(30)"), _limits(cfg, timeout_s=0.5))
    with pytest.raises(RenderError, match="timeout after 2 attempt"):
        r.render(_spec(cfg))
    assert r.last_exit == "cap_reached"


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("import sys; sys.stdout.write('not a zip')", "malformed output"),
        (
            "import sys, io, zipfile; b = io.BytesIO(); zipfile.ZipFile(b, 'w').writestr('x', 'y');"
            " sys.stdout.buffer.write(b.getvalue())",
            "malformed output",
        ),
        ("import sys; sys.stdout.buffer.write(b'x' * 3_000_000)", "output too large"),
        ("import sys; sys.stderr.write('e' * 200_000)", "diagnostics too large"),
        ("import sys; sys.exit(3)", "exit 3"),
    ],
)
def test_renderer_failures_are_typed(cfg: ExportConfig, code: str, reason: str) -> None:
    r = SubprocessWordRenderer(_script(code), _limits(cfg, max_output_bytes=1_000_000))
    with pytest.raises(RenderError, match=reason):
        r.render(_spec(cfg))


def test_renderer_environment_is_cleared(
    cfg: ExportConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://app_rw:secret@db/ai")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/secret.json")
    out = tmp_path / "env.json"
    code = (
        f"import json, os, sys; open({str(out)!r}, 'w').write(json.dumps(dict(os.environ)));"
        " sys.exit(1)"
    )
    r = SubprocessWordRenderer(_script(code), _limits(cfg, reruns=0))
    with pytest.raises(RenderError):
        r.render(_spec(cfg))
    env = json.loads(out.read_text())
    assert set(env) <= set(clean_env()), env
    assert "DATABASE_URL" not in env and "GOOGLE_APPLICATION_CREDENTIALS" not in env


def test_renderer_reruns_once_after_a_transient_failure(cfg: ExportConfig, tmp_path: Path) -> None:
    marker = tmp_path / "first"
    code = (
        "import os, sys, runpy\n"
        f"m = {str(marker)!r}\n"
        "if not os.path.exists(m):\n"
        "    open(m, 'w').close(); sys.exit(1)\n"
        "sys.argv = ['render_cli']\n"
        "runpy.run_module('exports.word.render_cli', run_name='__main__')"
    )
    r = SubprocessWordRenderer(_script(code), cfg.rules.renderer)
    body = r.render(_spec(cfg))
    assert body[:2] == b"PK" and r.last_exit == "converged" and marker.exists()


def test_contract_violation_is_refused_by_the_renderer(cfg: ExportConfig) -> None:
    r = SubprocessWordRenderer(python_renderer(), cfg.rules.renderer)
    with pytest.raises(RenderError, match="exit 2"):
        r._once(b'{"schema_version": 1, "blocks": []}')


def test_node_renderer_is_not_configured_until_ported(tmp_path: Path) -> None:
    with pytest.raises(NotConfigured):
        node_renderer(tmp_path)


def test_render_cli_main_in_process(
    cfg: ExportConfig, monkeypatch: pytest.MonkeyPatch, capsysbinary: pytest.CaptureFixture[bytes]
) -> None:
    spec = _spec(cfg)

    class In:
        def __init__(self, data: bytes) -> None:
            self.buffer = io.BytesIO(data)

    monkeypatch.setattr(sys, "stdin", In(spec.model_dump_json().encode()))
    assert render_cli.main() == 0
    assert capsysbinary.readouterr().out[:2] == b"PK"
    monkeypatch.setattr(sys, "stdin", In(b"{not json"))
    assert render_cli.main() == render_cli.EXIT_BAD_INPUT

    def boom(_: DocumentSpec) -> bytes:
        raise OSError("disk")

    monkeypatch.setattr(render_cli, "render", boom)
    monkeypatch.setattr(sys, "stdin", In(spec.model_dump_json().encode()))
    assert render_cli.main() == render_cli.EXIT_RENDER


def test_the_contract_schema_file_is_current() -> None:
    from exports.word.contract import json_schema

    path = Path(render_cli.__file__).parent / "schema" / "document.schema.json"
    assert path.read_text() == json_schema(), "regenerate the contract schema"


# ---- storage: idempotent, versioned, kill-safe ---------------------------------------------------


@pytest.mark.asyncio
async def test_a_repeated_build_writes_nothing(make_builder: Make) -> None:
    content = account()
    store = MemoryStore()
    first = await make_builder(content, store).build_all(content.tenant_id, content.run_id)
    rows, puts = list(store.rows), store.puts
    second = await make_builder(content, store).build_all(content.tenant_id, content.run_id)
    assert second.reused and [d.id for d in second.deliverables] == [
        d.id for d in first.deliverables
    ]
    assert store.rows == rows and store.puts == puts


@pytest.mark.asyncio
async def test_a_redelivery_adds_versions_that_supersede(make_builder: Make) -> None:
    store = MemoryStore()
    first_c = account()
    first = await make_builder(first_c, store).build_all(first_c.tenant_id, first_c.run_id)
    assert first.change_note == "First delivery for this account."
    again = account(n_people=5, run="00000000-0000-4000-8000-00000000a002")
    second = await make_builder(again, store).build_all(again.tenant_id, again.run_id)
    assert second.status == "complete"
    old = {d.kind: d for d in first.deliverables}
    for d in second.deliverables:
        assert d.version == 2 and d.supersedes == old[d.kind].id
    assert len(store.rows) == 12  # nothing overwritten: both versions kept
    assert "People List: 1 rows added, 0 removed, 0 changed" in (second.change_note or "")


@pytest.mark.asyncio
async def test_a_kill_mid_build_marks_nothing_complete(make_builder: Make) -> None:
    content = account()
    store = MemoryStore(fail_after_objects=True)
    with pytest.raises(KilledMidBuild):
        await make_builder(content, store).build_all(content.tenant_id, content.run_id)
    assert store.rows == []  # no deliverable marked complete
    puts = store.puts
    store.fail_after_objects = False
    result = await make_builder(content, store).build_all(content.tenant_id, content.run_id)
    assert result.status == "complete" and len(store.rows) == 6
    assert store.puts == puts  # the retry reuses the content-addressed objects
    assert set(store.objects) == {d.object_key for d in result.deliverables}  # no orphan left


# ---- logging canary ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_text_never_reaches_the_logs(
    make_builder: Make, caplog: pytest.LogCaptureFixture
) -> None:
    canary = "CANARY ROW TEXT 7f3a"
    content = account()
    facts = list(content.facts)
    facts[0] = facts[0].model_copy(update={"value_text": f"Synthetic {canary}"})
    content = content.model_copy(update={"facts": tuple(facts)})
    with caplog.at_level(logging.DEBUG):
        result, _ = await build(make_builder, content)
    assert canary not in caplog.text
    line = next(r.getMessage() for r in caplog.records if "exports.build" in r.getMessage())
    assert json.loads(line)["outcome"] == "complete"
    assert result.status == "complete"
