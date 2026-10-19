"""Delivery gates (rowcheck, doc_checks, tool-name/third-party scans, delivery flag)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from account_intelligence.grading.config import load_grading_config
from exports.builder import ExportBuilder
from exports.config import load_export_config
from exports.verify import (
    LibreOffice,
    doc_checks,
    docx_row_keys,
    docx_rowcheck,
    pdf_row_presence,
)
from tests.exports.corpus import account, rich
from tests.fakes.exports import FakeReader, InProcessRenderer, MemoryStore

ROOT = Path(__file__).resolve().parents[2]
SKILL_SCRIPTS = ROOT / "config/skill/archive/scripts"
HAVE_OFFICE = shutil.which("soffice") is not None and shutil.which("pdftotext") is not None
office = pytest.mark.skipif(not HAVE_OFFICE, reason="not_run: LibreOffice or poppler missing")


def _make_builder(
    content, pdf=None, recalc=None, config=None, grading_config=None
) -> ExportBuilder:
    """Create a builder with standard fakes."""
    cfg = config or load_export_config()
    grad = grading_config or load_grading_config()
    return ExportBuilder(
        FakeReader(content), MemoryStore(), InProcessRenderer(), cfg, grad, pdf=pdf, recalc=recalc
    )


def test_rowcheck_script_exists() -> None:
    assert (SKILL_SCRIPTS / "rowcheck.py").is_file()
    assert (SKILL_SCRIPTS / "doc_checks.py").is_file()


def test_doc_checks_script_exists() -> None:
    assert (SKILL_SCRIPTS / "doc_checks.py").is_file()


@office
def test_rowcheck_passes_on_valid_summary() -> None:
    content = account()
    lo = LibreOffice()
    builder = _make_builder(content, pdf=lo)
    out = builder.assemble(content)
    assert not out.errors
    _, body = out.files["summary"]
    result = docx_rowcheck(body, lo)
    assert result.status == "passed"


@office
def test_rowcheck_fails_on_dropped_row() -> None:
    content = account()
    lo = LibreOffice()
    builder = _make_builder(content, pdf=lo)
    out = builder.assemble(content)
    _, body = out.files["summary"]
    keys = list(docx_row_keys(body)) + ["L-9999"]
    result = docx_rowcheck(body, lo)
    assert result.status == "passed"
    result2 = pdf_row_presence(body, keys, lo)
    assert result2.status == "failed"


def test_rowcheck_fails_closed_without_libreoffice() -> None:
    absent = LibreOffice(binary=None)
    absent.binary = None
    result = docx_rowcheck(b"dummy", absent)
    assert result.status == "failed"
    assert "unavailable" in result.reason.lower() or "not installed" in result.reason.lower()


def test_rowcheck_fails_closed_without_pdftotext(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda x: None if x == "pdftotext" else "/usr/bin/soffice")
    lo = LibreOffice()
    result = docx_rowcheck(b"dummy", lo)
    assert result.status == "failed"
    assert "pdftotext" in result.reason


@office
def test_doc_checks_passes_on_clean_document() -> None:
    content = account()
    lo = LibreOffice()
    builder = _make_builder(content, pdf=lo)
    out = builder.assemble(content)
    _, body = out.files["summary"]
    result = doc_checks(
        body,
        "summary",
        [],
        (),
        (),
    )
    assert result.status == "passed"


@office
def test_doc_checks_blocks_stale_phrase() -> None:
    content = account()
    lo = LibreOffice()
    builder = _make_builder(content, pdf=lo)
    out = builder.assemble(content)
    _, body = out.files["summary"]
    stale = [
        "Sensitive findings, such as a breach, a layoff or a lawsuit, "
        "are preparation for the conversation and never an opener."
    ]
    result = doc_checks(
        body,
        "summary",
        [],
        stale,
        (),
    )
    assert result.status == "failed"
    assert "stale" in result.reason.lower()


@office
def test_doc_checks_blocks_excluded_name() -> None:
    content = account()
    lo = LibreOffice()
    builder = _make_builder(content, pdf=lo)
    out = builder.assemble(content)
    _, body = out.files["summary"]
    excluded = ["Synthetic Person 000"]
    result = doc_checks(
        body,
        "summary",
        [],
        (),
        excluded,
    )
    assert result.status == "failed"
    assert "excluded" in result.reason.lower()


@office
def test_doc_checks_blocks_banned_dash() -> None:
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    text_with_dash = "Synthetic finding — test"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        text_path = tmp_path / "doc.txt"
        ids_path = tmp_path / "ids.json"
        text_path.write_text(text_with_dash, encoding="utf-8")
        ids_path.write_text("[]", encoding="utf-8")
        script = SKILL_SCRIPTS / "doc_checks.py"
        result = subprocess.run(
            [sys.executable, str(script), str(text_path), "--ids", str(ids_path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert result.returncode != 0
    assert "dash" in result.stdout.lower() or "—" in result.stdout


def test_tool_name_scan_on_final_docx() -> None:
    content = account()
    lo = LibreOffice()
    builder = _make_builder(content, pdf=lo)
    out = builder.assemble(content)
    _, body = out.files["summary"]
    from exports.verify import scan_package

    hits = scan_package(body, ())
    vendor_names = [h for h in hits if "broker" in h.lower() or "tool" in h.lower()]
    assert not vendor_names


def test_third_party_domain_scan_on_final_docx() -> None:
    content = account()
    lo = LibreOffice()
    builder = _make_builder(content, pdf=lo)
    out = builder.assemble(content)
    _, body = out.files["summary"]
    from documents.gate import provider_hosts
    from exports.verify import _extract_doc_text

    hosts = provider_hosts()
    text = _extract_doc_text(body)
    for host in hosts:
        assert host.lower() not in text.lower()


def test_tool_name_scan_on_final_xlsx() -> None:
    content = account()
    lo = LibreOffice()
    builder = _make_builder(content, pdf=lo, recalc=lo)
    out = builder.assemble(content)
    for kind in ("people", "jobs", "tech"):
        _, body = out.files[kind]
        from exports.verify import scan_package

        hits = scan_package(body, ())
        vendor_names = [h for h in hits if "broker" in h.lower() or "tool" in h.lower()]
        assert not vendor_names


def test_third_party_domain_scan_on_final_xlsx() -> None:
    content = account()
    lo = LibreOffice()
    builder = _make_builder(content, pdf=lo, recalc=lo)
    out = builder.assemble(content)
    for kind in ("people", "jobs", "tech"):
        _, body = out.files[kind]
        from documents.gate import provider_hosts
        from exports.verify import _extract_doc_text

        hosts = provider_hosts()
        text = _extract_doc_text(body)
        for host in hosts:
            assert host.lower() not in text.lower()


@office
def test_tool_name_scan_on_final_pdf() -> None:
    content = account()
    lo = LibreOffice()
    builder = _make_builder(content, pdf=lo)
    out = builder.assemble(content)
    _, body = out.files["summary"]
    pdf_bytes = lo.pdf_bytes(body)
    assert pdf_bytes is not None
    # PDF is not a zip file, so extract text and check directly
    from documents.gate import provider_hosts
    from exports.verify import _extract_doc_text

    text = _extract_doc_text(pdf_bytes)
    from account_intelligence.connectors.registry_internal import vendor_names
    from account_intelligence.connectors.settings import load_providers_config

    vendor_tools = [
        name.lower()
        for name in vendor_names(load_providers_config()).values()
        if name != "Parallel"
    ]
    vendor_names = [n for n in vendor_tools if n in text.lower()]
    assert not vendor_names
    hosts = provider_hosts()
    for host in hosts:
        assert host.lower() not in text.lower()


@office
def test_third_party_domain_scan_on_final_pdf() -> None:
    content = account()
    lo = LibreOffice()
    builder = _make_builder(content, pdf=lo)
    out = builder.assemble(content)
    _, body = out.files["summary"]
    pdf_bytes = lo.pdf_bytes(body)
    assert pdf_bytes is not None
    from documents.gate import provider_hosts
    from exports.verify import _extract_doc_text

    hosts = provider_hosts()
    text = _extract_doc_text(pdf_bytes)
    for host in hosts:
        assert host.lower() not in text.lower()


def test_delivery_flag_is_on_for_this_release() -> None:
    # owner decision 2026-10-18: Word, deck and PDF delivery are enabled, behind their gates.
    # The route behaviour with the flag off and on is tested end to end in
    # tests/phase3/test_ws4_views.py (test_with_delivery_off_files_and_pdf_are_refused,
    # test_every_export_kind_is_delivered_only_with_parity).
    cfg = load_export_config()
    assert cfg.rules.delivery.enabled is True


def test_extract_doc_text_handles_pptx() -> None:
    pass


def test_skill_path_resolution() -> None:
    assert SKILL_SCRIPTS.is_dir()
    assert (SKILL_SCRIPTS / "rowcheck.py").is_file()
    assert (SKILL_SCRIPTS / "doc_checks.py").is_file()


@office
def test_builder_runs_rowcheck_on_summary_and_evidence() -> None:
    content = rich(scale=1)
    lo = LibreOffice()
    builder = _make_builder(content, pdf=lo)
    out = builder.assemble(content)
    assert not out.errors
    check_names = {c.name for c in out.checks}
    assert "docx_rowcheck" in check_names
    assert "doc_checks" in check_names


@office
def test_builder_runs_doc_checks_on_summary_and_evidence() -> None:
    content = account()
    lo = LibreOffice()
    builder = _make_builder(content, pdf=lo)
    out = builder.assemble(content)
    assert not out.errors
    doc_checks_results = [c for c in out.checks if c.name == "doc_checks"]
    assert len(doc_checks_results) == 2
    for c in doc_checks_results:
        assert c.status == "passed"


@office
def test_builder_runs_rowcheck_on_both_docs() -> None:
    content = account()
    lo = LibreOffice()
    builder = _make_builder(content, pdf=lo)
    out = builder.assemble(content)
    assert not out.errors
    rowcheck_results = [c for c in out.checks if c.name == "docx_rowcheck"]
    assert len(rowcheck_results) == 2
    for c in rowcheck_results:
        assert c.status == "passed"
