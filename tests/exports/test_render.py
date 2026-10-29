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


# ---- the reference look: landscape, heading styles, links, fills, footer ------------------------

R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _archived(content: SnapshotContent) -> SnapshotContent:
    """Source S-0001 and posting post-00000 with an archived copy; the rest without."""
    sources = tuple(
        s.model_copy(update={"archive_url": f"https://archive.example/copies/{s.code}"})
        if s.code == "S-0001"
        else s
        for s in content.sources
    )
    postings = tuple(
        p.model_copy(update={"archive_url": f"https://archive.example/copies/{p.posting_key}"})
        if p.posting_key == "post-00000"
        else p
        for p in content.postings
    )
    return content.model_copy(update={"sources": sources, "postings": postings})


def _parts(body: bytes) -> tuple[Any, Any, list[Any], bool]:
    """document.xml, its relationships, the footer parts and whether a header part exists."""
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        doc = etree.fromstring(z.read("word/document.xml"))
        rels = etree.fromstring(z.read("word/_rels/document.xml.rels"))
        footers = [etree.fromstring(z.read(n)) for n in z.namelist() if n.startswith("word/footer")]
        header = any(n.startswith("word/header") for n in z.namelist())
    return doc, rels, footers, header


def _text_of(el: Any) -> str:
    return "".join(t.text or "" for t in el.iter(f"{W}t"))


def _links(doc: Any, rels: Any) -> list[tuple[str, str]]:
    """(text, target) of every hyperlink; every one must resolve to an external relationship."""
    targets = {
        r.get("Id"): r.get("Target")
        for r in rels.iter(f"{RELS}Relationship")
        if r.get("TargetMode") == "External" and r.get("Type", "").endswith("/hyperlink")
    }
    out = []
    for h in doc.iter(f"{W}hyperlink"):
        rid = h.get(f"{R}id")
        assert rid in targets, "a hyperlink without an external relationship"
        out.append((_text_of(h), targets[rid]))
    return out


@pytest.mark.asyncio
async def test_the_documents_follow_the_reference_look(
    make_builder: Make, cfg: ExportConfig
) -> None:
    """Both Word files: Letter landscape with the reference margins, Word heading styles with a
    page break before every section after the first, the theme's fonts on every run, grade and
    status fills by cell value, a right-aligned PAGE footer and no header part."""
    content = _archived(rich())
    _, files = await build(make_builder, content)
    theme = cfg.theme.word
    for kind in ("summary", "evidence"):
        doc, _, footers, header = _parts(files[kind])
        sect = doc.find(f".//{W}sectPr")
        size, margin = sect.find(f"{W}pgSz"), sect.find(f"{W}pgMar")
        assert size.get(f"{W}orient") == "landscape"
        assert (size.get(f"{W}w"), size.get(f"{W}h")) == ("15840", "12240"), kind
        assert (margin.get(f"{W}top"), margin.get(f"{W}left")) == ("1296", "1440")
        styles = [
            p.find(f"{W}pPr/{W}pStyle").get(f"{W}val")
            for p in doc.iter(f"{W}p")
            if p.find(f"{W}pPr/{W}pStyle") is not None
        ]
        h1 = styles.count("Heading1")
        assert h1 >= 9 and styles.count("Heading2") >= 8, kind
        breaks = [p for p in doc.iter(f"{W}p") if p.find(f"{W}pPr/{W}pageBreakBefore") is not None]
        assert len(breaks) == h1 - 1  # every section after the first starts a page
        assert all(p.find(f"{W}pPr/{W}pStyle").get(f"{W}val") == "Heading1" for p in breaks)
        faces = {f.get(f"{W}ascii") for f in doc.iter(f"{W}rFonts")}
        assert faces == {theme.fonts.body.name}
        # the footer is "Page" and a PAGE field, right-aligned; there is no header part
        (footer,) = footers
        assert _text_of(footer).startswith(f"{cfg.rules.documents.labels.page_label} ")
        assert [f.get(f"{W}fldCharType") for f in footer.iter(f"{W}fldChar")] == [
            "begin",
            "separate",
            "end",
        ]
        assert "PAGE" in "".join(i.text or "" for i in footer.iter(f"{W}instrText"))
        assert footer.find(f".//{W}jc").get(f"{W}val") == "right"
        assert not header
        assert cfg.rules.documents.labels.attribution_line not in _text_of(doc)
    # fills: every body cell whose whole text is a grade or status word carries its fill; a
    # header cell carries the header fill; no other cell is filled
    doc, _, _, _ = _parts(files["summary"])
    seen: dict[str, set[str]] = {}
    for tbl in doc.iter(f"{W}tbl"):
        rows = list(tbl.iter(f"{W}tr"))
        for tc in rows[0].iter(f"{W}tc"):
            assert tc.find(f"{W}tcPr/{W}shd").get(f"{W}fill") == theme.table.header_fill
        for tr in rows[1:]:
            for tc in tr.iter(f"{W}tc"):
                shd = tc.find(f"{W}tcPr/{W}shd")
                text = _text_of(tc)
                if text in theme.fills:
                    assert shd is not None and shd.get(f"{W}fill") == theme.fills[text], text
                    seen.setdefault(text, set()).add(shd.get(f"{W}fill"))
                else:
                    assert shd is None, text
    assert {"Confirmed", "Probable", "Hypothesis", "Lead", "Done", "None"} <= set(seen)
    # the label column (after the row key) is bold in body rows
    table = next(t for t in doc.iter(f"{W}tbl") if _text_of(list(t.iter(f"{W}tc"))[0]) == "Ref")
    body_row = list(table.iter(f"{W}tr"))[1]
    bold = [tc.find(f".//{W}rPr/{W}b") is not None for tc in body_row.iter(f"{W}tc")]
    assert bold[1] and not bold[0] and not any(bold[2:])


@pytest.mark.asyncio
async def test_every_link_is_a_hyperlink_with_its_archived_copy_beside_it(
    make_builder: Make, cfg: ExportConfig
) -> None:
    """The owner's rule: every link in the outputs is a working link, with an archived copy
    beside it when one is stored. Key people link "Profile"; the Summary's source links and the
    Evidence register link the source; every posting of parts B, C and E links; a source or
    posting with an archived copy carries the "Archived copy" link; one without carries none;
    Part I writes the addresses as plain text; a link run is the theme's link font."""
    content = _archived(rich())
    result, files = await build(make_builder, content)
    labels = cfg.rules.documents.labels
    link_font = cfg.theme.word.fonts.link
    for kind in ("summary", "evidence"):
        doc, rels, _, _ = _parts(files[kind])
        links = _links(doc, rels)
        assert links and all(t.startswith("https://") for _, t in links), kind
        for h in doc.iter(f"{W}hyperlink"):
            rpr = h.find(f"{W}r/{W}rPr")
            assert rpr.find(f"{W}u").get(f"{W}val") == "single"
            assert rpr.find(f"{W}color").get(f"{W}val") == link_font.color
    doc, rels, _, _ = _parts(files["summary"])
    summary_links = _links(doc, rels)
    profiles = [t for text, t in summary_links if text == labels.profile_link]
    key_people = [r for r in result.manifest.refs["summary"] if r.startswith("K-")]
    assert profiles and len(profiles) == len(key_people)  # one Profile link per key person
    assert not any(text == labels.archived_copy for text, _ in summary_links)  # live links only
    doc, rels, _, _ = _parts(files["evidence"])
    evidence_links = _links(doc, rels)
    archived = [t for text, t in evidence_links if text == labels.archived_copy]
    # the register (A), the posting read in full (B) and its lane row (C): one copy each
    assert sorted(archived) == sorted(
        [
            "https://archive.example/copies/S-0001",
            "https://archive.example/copies/post-00000",
            "https://archive.example/copies/post-00000",
        ]
    )
    assert sum(1 for text, _ in evidence_links if text == labels.archived_copy) == 3
    text = "\n".join(_text_of(p) for p in doc.iter(f"{W}p"))
    # Part I: the addresses as plain lines, the archived one under its source
    assert labels.url_line.format(code="S-0001", url="https://rich.example/news/item-0") in text
    assert labels.archived_line.format(url="https://archive.example/copies/S-0001") in text
    assert text.count("archived: ") == 1
    # a source cell without a stored copy links once: the live page
    cells = [
        _links(tc, rels)
        for tbl in doc.iter(f"{W}tbl")
        for tr in list(tbl.iter(f"{W}tr"))[1:]
        for tc in tr.iter(f"{W}tc")
        if tc.find(f".//{W}hyperlink") is not None
    ]
    assert cells and all(len(c) in (1, 2) for c in cells)
    assert sum(1 for c in cells if len(c) == 2) == 3


@pytest.mark.asyncio
async def test_a_redelivery_opens_with_the_change_line(make_builder: Make) -> None:
    """A re-shared file opens with one line on what is different (the owner's rule): the change
    note is the first paragraph under the title of both documents on the second delivery and
    absent on the first."""
    from exports.verify import docx_text

    store = MemoryStore()
    first_c = account()
    first = await make_builder(first_c, store).build_all(first_c.tenant_id, first_c.run_id)
    again = account(n_people=5, run="00000000-0000-4000-8000-00000000a002")
    second = await make_builder(again, store).build_all(again.tenant_id, again.run_id)
    assert first.status == second.status == "complete"
    files = {d.kind: store.objects[d.object_key] for d in first.deliverables}
    for kind in ("summary", "evidence"):
        assert "Changes since the previous delivery" not in docx_text(files[kind])
        assert "First delivery" not in docx_text(files[kind])
    files = {d.kind: store.objects[d.object_key] for d in second.deliverables}
    note = second.change_note or ""
    assert note.startswith("Changes since the previous delivery: ")
    for kind in ("summary", "evidence"):
        with zipfile.ZipFile(io.BytesIO(files[kind])) as z:
            doc = etree.fromstring(z.read("word/document.xml"))
        paragraphs = [_text_of(p) for p in doc.iter(f"{W}p") if _text_of(p)]
        assert paragraphs[1] == note, kind  # right under the title, before the opening line
        assert paragraphs[2].startswith("Research date ")


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
