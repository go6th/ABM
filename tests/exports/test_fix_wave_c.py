"""Fix wave C (2026-10-29): the review's and the end-to-end test's defects of the deliverable
files, each with a test that fails on the code before the fix. All data synthetic."""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from typing import Any

import pytest
from openpyxl import load_workbook

from account_intelligence.grading.config import load_grading_config
from account_intelligence.urls import clean_http_url
from exports.builder import ExportBuilder
from exports.content import SnapshotContent, SourceRow
from exports.models import BuildResult
from exports.urlcheck import is_safe_http_url
from tests.exports.corpus import account
from tests.fakes.exports import FakeReader, InProcessRenderer, MemoryStore

Make = Callable[..., ExportBuilder]


async def run(make: Make, content: SnapshotContent) -> tuple[BuildResult, MemoryStore]:
    store = MemoryStore()
    result = await make(content, store).build_all(content.tenant_id, content.run_id)
    return result, store


def sheet_of(
    store: MemoryStore, result: BuildResult, kind: str, name: str, *, values: bool = False
) -> Any:
    deliverable = next(d for d in result.deliverables if d.kind == kind)
    book = load_workbook(io.BytesIO(store.objects[deliverable.object_key]), data_only=values)
    return book[name]


# ---- 2: one bad address never loses the five files ------------------------------------------

LONG = "https://x.example/" + "a" * 2100
# (raw address, the address the file links to, or None when the row is withheld as bad_url)
VARIANTS: tuple[tuple[str, str | None], ...] = (
    ("https://x.example/a b/c", "https://x.example/a%20b/c"),
    ("HTTPS://X.EXAMPLE/Path", "https://x.example/Path"),
    ("  https://x.example/a  ", "https://x.example/a"),
    ("https://x.example/a\n", "https://x.example/a"),
    ("javascript:alert(1)", None),
    ("ftp://x.example/a", None),
    ("file:///etc/passwd", None),
    ("data:text/html,hi", None),
    ("https://x.example/a\nb", None),
    ("https://x.example/a\tb", None),
    ("https://user:pw@x.example/a", None),
    ("https:///nohost", None),
    ("//x.example/a", None),
    ("https://x.example/a\\b", None),
    ("https://x.example/a‮b", None),
    ("https://x.example/a​b", None),
    (LONG, None),
)


def test_the_address_cleaner_repairs_spelling_and_refuses_the_rest() -> None:
    for raw, expected in VARIANTS:
        assert clean_http_url(raw) == expected, raw
        if expected is not None:
            assert is_safe_http_url(expected), raw
    assert clean_http_url(None) is None and clean_http_url("") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("raw", "expected"), VARIANTS, ids=[f"v{n}" for n in range(len(VARIANTS))])
async def test_a_bad_posting_address_is_repaired_or_the_row_withheld(
    make_builder: Make, raw: str, expected: str | None
) -> None:
    c = account(n_postings=3)
    postings = list(c.postings)
    postings[0] = postings[0].model_copy(update={"url": raw, "full_text_read": True})
    # the registry holds the repaired address (the job repository registers it)
    sources = list(c.sources)
    if expected is not None:
        sources.append(SourceRow(code="S-0900", url=expected, published=None, kind="secondary"))
    content = c.model_copy(update={"postings": tuple(postings), "sources": tuple(sources)})
    result, store = await run(make_builder, content)
    assert result.status == "complete", result.errors
    listed = result.manifest.row_ids["jobs"]
    if expected is None:
        assert "jobs:post-00000:bad_url" in result.manifest.withheld_blocks
        assert "post-00000" not in listed and len(listed) == 2
        assert not_listed(store, result) == 1  # the Counts sheet says how many were left out
    else:
        assert "post-00000" in listed and len(listed) == 3
        assert not_listed(store, result) == 0
        jobs = sheet_of(store, result, "jobs", "Jobs")
        links = {c.hyperlink.target for row in jobs.iter_rows() for c in row if c.hyperlink}
        assert expected in links
        assert all(is_safe_http_url(t) for t in links)


def not_listed(store: MemoryStore, result: BuildResult) -> int:
    """The Counts sheet's "Postings not listed" value of the Jobs file."""
    counts = sheet_of(store, result, "jobs", "Counts")
    for row in counts.iter_rows(values_only=True):
        if row[0] and str(row[0]).startswith("Postings not listed"):
            return int(row[1])
    raise AssertionError("no 'Postings not listed' line")


@pytest.mark.asyncio
async def test_every_other_address_field_survives_a_bad_value(make_builder: Make) -> None:
    """Sources, people (profile and second profile), findings, detections and the archived-copy
    addresses all carry a hostile value at once: the five files are still built."""
    c = account(n_people=4, n_postings=3)
    sources = [
        s.model_copy(update={"url": s.url.replace("https://", "HTTPS://", 1)})
        if n == 0
        else s.model_copy(update={"url": s.url + "/a b"})
        if n == 1
        else s
        for n, s in enumerate(c.sources)
    ]
    sources = [s.model_copy(update={"archive_url": "https://x.example/a b"}) for s in sources]
    people = list(c.people)
    people[0] = people[0].model_copy(update={"second_profile_url": "javascript:alert(1)"})
    people[1] = people[1].model_copy(update={"profile_url": "file:///etc/passwd"})
    findings = [f.model_copy(update={"url": "ftp://x.example/post"}) for f in c.findings]
    detections = list(c.detections)
    detections[0] = detections[0].model_copy(update={"source_url": "javascript:void(0)"})
    postings = [p.model_copy(update={"archive_url": "https://x.example/a b"}) for p in c.postings]
    content = c.model_copy(
        update={
            "sources": tuple(sources),
            "people": tuple(people),
            "findings": tuple(findings),
            "detections": tuple(detections),
            "postings": tuple(postings),
        }
    )
    result, _store = await run(make_builder, content)
    assert result.status == "complete", result.errors
    withheld = result.manifest.withheld_blocks
    assert f"people:{people[1].id}:bad_url" in withheld
    assert f"tech:{detections[0].id}:bad_url" in withheld
    assert f"summary.linkedin_findings:{c.findings[0].person_id}:bad_url" in withheld


# ---- 1: refs follow the listed rows ----------------------------------------------------------


@pytest.mark.asyncio
async def test_refs_never_skip_a_number_when_a_row_is_withheld(make_builder: Make) -> None:
    c = account(n_postings=4, n_people=4, n_detections=3)
    postings = list(c.postings)
    postings[1] = postings[1].model_copy(update={"url": None})
    people = list(c.people)
    people[1] = people[1].model_copy(update={"source_code": None, "profile_url": ""})
    dets = list(c.detections)
    dets[0] = dets[0].model_copy(update={"source_url": None})
    content = c.model_copy(
        update={"postings": tuple(postings), "people": tuple(people), "detections": tuple(dets)}
    )
    result, _store = await run(make_builder, content)
    assert result.status == "complete", result.errors
    for kind, prefix in (("jobs", "J"), ("people", "P")):
        refs = result.manifest.refs[kind]
        assert refs == tuple(f"{prefix}-{n:04d}" for n in range(1, len(refs) + 1)), (kind, refs)


# ---- 3: the delivered workbooks carry the result of every formula ---------------------------


def _cached(xlsx: bytes, sheet: str) -> dict[str, Any]:
    book = load_workbook(io.BytesIO(xlsx), data_only=True)
    return {c.coordinate: c.value for row in book[sheet].iter_rows() for c in row}


@pytest.mark.asyncio
async def test_every_formula_cell_shows_its_value_without_a_spreadsheet_engine(
    make_builder: Make, cfg: Any
) -> None:
    """openpyxl ``data_only`` (and pandas) read the stored results: before the fix every
    Summary, Counts and Ranking formula cell read as empty."""
    import pandas as pd

    from exports.excel.jobs import expected_jobs
    from exports.excel.people import expected_people
    from exports.excel.tech import expected_tech
    from tests.exports.corpus import rich

    content = rich()
    out = ExportBuilder(
        FakeReader(content), MemoryStore(), InProcessRenderer(), cfg, load_grading_config()
    ).assemble(content)
    assert not out.errors, out.errors
    expectations = {
        "jobs": expected_jobs(out.mapped["jobs"].model, cfg),
        "people": expected_people(out.mapped["people"].model, cfg),
        "tech": expected_tech(out.mapped["tech"].model, cfg),
    }
    seen = 0
    for kind, expected in expectations.items():
        _name, body = out.files[kind]
        assert expected, kind
        for (sheet, coord), value in expected.items():
            got = _cached(body, sheet)[coord]
            if value == "":
                assert got in (None, ""), (kind, sheet, coord)
            elif isinstance(value, float):
                assert got == pytest.approx(value), (kind, sheet, coord)
            else:
                assert got == value, (kind, sheet, coord, got, value)
            seen += 1
        # pandas, the way a data tool or an indexer reads the file
        frames = pd.read_excel(io.BytesIO(body), sheet_name=None, header=None)
        sheet, coord = next(iter(expected))
        assert sheet in frames
    assert seen > 40


def test_stored_results_are_removed_again_before_a_spreadsheet_engine_calculates() -> None:
    """LibreOffice keeps a stored result as it is, so the recalculation check strips them: a
    wrong stored value must not make the check read the file back instead of calculating it."""
    import shutil

    from openpyxl import Workbook

    from exports.ooxml import cache_formula_values, strip_cached_values
    from exports.verify import LibreOffice, recalc_check

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Counts"
    ws["A1"] = 5
    ws["B1"] = "=A1*2"
    ws["B2"] = '=IF(A1>1,"A","")'
    buf = io.BytesIO()
    wb.save(buf)
    stamp = (2000, 1, 1, 0, 0, 0)
    wrong, cached, missing = cache_formula_values(
        buf.getvalue(), {("Counts", "B1"): 999, ("Counts", "B2"): "A"}, entry_time=stamp
    )
    assert (cached, missing) == (2, ())
    assert _cached(wrong, "Counts")["B1"] == 999 and _cached(wrong, "Counts")["B2"] == "A"
    assert _cached(strip_cached_values(wrong), "Counts")["B1"] is None
    if shutil.which("soffice"):
        # the true value is 10: the stored 999 is ignored, the expected 10 passes
        assert recalc_check(wrong, {("Counts", "B1"): 10}, LibreOffice()).status == "passed"
        assert recalc_check(wrong, {("Counts", "B1"): 999}, LibreOffice()).status == "failed"


# ---- 5: hyperlink relationships in linear time ----------------------------------------------


def test_the_linear_link_ids_are_the_ids_python_docx_would_give() -> None:
    """One relationship per distinct target, in order of first use: the document is byte for
    byte the one ``relate_to`` made (and ``relate_to`` is quadratic in the links)."""
    import time

    from docx import Document
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    from exports.word import render_cli

    targets = [f"https://x.example/{n % 40}" for n in range(300)]
    new = Document()
    ids_new = [render_cli._link_id(new.part, t) for t in targets]
    old = Document()
    ids_old = [old.part.relate_to(t, RT.HYPERLINK, is_external=True) for t in targets]
    assert ids_new == ids_old
    assert len(new.part.rels) == len(old.part.rels)
    assert new.part.rels.xml == old.part.rels.xml
    # distinct targets by the thousand take no longer than a fraction of a second
    big = Document()
    started = time.perf_counter()
    for n in range(20_000):
        render_cli._link_id(big.part, f"https://x.example/p/{n}")
    assert time.perf_counter() - started < 3.0


def _canonical(docx: bytes, member: str) -> str:
    import zipfile

    from lxml import etree

    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        return etree.tostring(etree.fromstring(z.read(member)), method="c14n").decode()


@pytest.mark.parametrize("kind", ["summary", "evidence"])
def test_a_table_written_as_text_is_the_table_written_as_elements(kind: str) -> None:
    """A big Word table is written as text (an element tree of a million cells took over a
    gigabyte). The document is the same canonical XML either way, relationships included."""
    from unittest.mock import patch

    from account_intelligence.grading.config import load_grading_config
    from exports.builder import map_snapshot
    from exports.config import load_export_config
    from exports.word import render_cli
    from exports.word.compose import compose_evidence, compose_summary
    from tests.exports.corpus import rich

    cfg = load_export_config()
    mapped, _ctx = map_snapshot(rich(), cfg, load_grading_config())
    compose = compose_summary if kind == "summary" else compose_evidence
    spec = compose(mapped[kind].model, cfg, "Synthetic", None)
    with patch.object(render_cli, "STREAM_ROWS", 10**9):
        tree = render_cli.render(spec)
    with patch.object(render_cli, "STREAM_ROWS", 0):
        text = render_cli.render(spec)
    assert _canonical(tree, "word/document.xml") == _canonical(text, "word/document.xml")
    rels = "word/_rels/document.xml.rels"
    assert _canonical(tree, rels) == _canonical(text, rels)
    assert "@@ROWS-" not in _canonical(text, "word/document.xml")  # every marker was replaced
    from exports.verify import docx_row_keys

    assert docx_row_keys(tree) == docx_row_keys(text) and docx_row_keys(text)


# ---- 5: the dash rule in time that does not grow with passages times text -------------------


def test_the_fast_dash_count_is_the_plain_dash_count() -> None:
    """``dash_count`` (blanking passages only on the lines that hold a dash) equals
    ``dash_count_plain`` (every passage replaced over the whole text) on texts that mix titles
    with dashes, authored dashes, quotes, bare hyphens and line breaks."""
    import random

    from exports.verify import dash_count, dash_count_plain

    rng = random.Random(5)
    titles = [
        "Manager - Salesforce",
        "Lead – Platforms",
        "Sr. Engineer - Cloud - EMEA",
        "Director",
        "Salesforce Admin",
        "A - B",
        "Quote: we invest — heavily",
        "plain title",
    ]
    for _ in range(400):
        passages = sorted({t for t in rng.sample(titles, k=rng.randint(0, len(titles)))}, key=len)
        pieces = []
        for _line in range(rng.randint(1, 12)):
            parts = rng.choices(
                titles + ["-", "and", "text - more", "x—y", "end."], k=rng.randint(1, 4)
            )
            pieces.append(" ".join(parts))
        text = "\n".join(pieces)
        for spaced in (True, False):
            fast = dash_count(text, passages, ("—", "–"), spaced)
            plain = dash_count_plain(text, passages, ("—", "–"), spaced)
            assert fast == plain, (text, passages, spaced, fast, plain)
    # passages with a space at an end, or a line break, take the plain path (same answer)
    odd = ["Foo ", "a\nb - c"]
    assert dash_count("Foo - bar\na\nb - c", odd, ("—",), True) == dash_count_plain(
        "Foo - bar\na\nb - c", odd, ("—",), True
    )


def test_many_passages_over_a_large_text_do_not_multiply() -> None:
    import time

    from exports.verify import dash_count

    titles = [f"Synthetic Role {n} - Platform {n % 7}" for n in range(6000)]
    text = "\n".join(f"R-{n:05d}\n{titles[n]}\nhttps://x.example/job-{n}" for n in range(6000))
    started = time.perf_counter()
    assert dash_count(text, sorted(titles, key=len), ("—", "–"), True) == 0
    assert time.perf_counter() - started < 5.0


def test_the_converter_time_grows_with_the_package_it_converts() -> None:
    """A flat 120 s timed out on the 7,000 posting Jobs workbook (133 s): the allowance is the
    base plus seconds per unpacked megabyte, capped."""
    import zipfile

    from exports.verify import MAX_TIMEOUT_S, SECONDS_PER_MB, LibreOffice

    def package(megabytes: int) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("xl/worksheets/sheet1.xml", b"<x/>" * (megabytes * 250_000))
        return buf.getvalue()

    office = LibreOffice(timeout_s=120.0, binary="/bin/true")
    assert office.timeout_for(package(1)) == pytest.approx(120.0 + SECONDS_PER_MB)
    assert office.timeout_for(package(50)) == pytest.approx(120.0 + 50 * SECONDS_PER_MB)
    assert office.timeout_for(package(5000)) == MAX_TIMEOUT_S
    assert office.timeout_for(b"not a zip") == pytest.approx(120.0, abs=0.01)
    assert LibreOffice(timeout_s=1.0, binary="/bin/true").timeout_for(package(1)) < 10


@pytest.mark.asyncio
async def test_a_check_that_did_not_run_is_listed_in_the_manifest_with_its_reason(
    make_builder: Make,
) -> None:
    """Before: a conversion that timed out left no trace in a delivered set. Now the manifest
    says ``not_run: <why>`` for each such check (and the build logs a warning)."""
    from tests.exports.test_delivery_gates import _make_builder
    from tests.exports.test_render_check_runtime import SlowConverter

    slow = SlowConverter(0.0)
    result = await _make_builder(account(), recalc=slow).build_all("t", "r")
    assert result.status == "complete", result.errors
    checks = result.manifest.checks
    assert checks["workbook_recalc"].startswith("not_run: the spreadsheet conversion")
    assert "timeout" in checks["workbook_recalc"]
    assert checks["workbook_cached_values"] == "passed"
    # a build with no converter wired does not list a recalculation check at all
    plain = await make_builder(account(), MemoryStore()).build_all("t", "r")
    assert plain.manifest.checks["workbook_cached_values"] == "passed"
    assert "workbook_recalc" not in plain.manifest.checks  # no recalc port wired: not a check


# ---- 6: one rule for an unknown status, and no dash as punctuation in a title ---------------


@pytest.mark.asyncio
async def test_an_unknown_status_prints_open_and_counts_as_open_everywhere(
    make_builder: Make,
) -> None:
    """Postings alternate open, closed, unknown. The Summary matrix, the Counts sheet and the
    Platform Ranking's "Open now" agree: Total = Closed + Open, unknown is Open."""
    from exports.builder import map_snapshot
    from exports.config import load_export_config

    content = account(n_postings=6)
    statuses = [p.status for p in content.postings]
    assert statuses.count("unknown") == 2 and statuses.count("closed") == 2
    result, store = await run(make_builder, content)
    assert result.status == "complete", result.errors
    jobs = sheet_of(store, result, "jobs", "Jobs")
    printed = [c.value for c in jobs["E"][1:]]
    assert sorted(set(printed)) == ["Closed", "Open"] and printed.count("Open") == 4
    assert "Status unknown" not in {c.value for row in jobs.iter_rows() for c in row}
    mapped, _ctx = map_snapshot(content, load_export_config(), load_grading_config())
    for m in mapped["jobs"].model.matrix:
        assert m.rows == m.open + m.closed, m
    counts = {
        str(r[0]): r[1]
        for r in sheet_of(store, result, "jobs", "Counts", values=True).iter_rows(values_only=True)
        if r[0]
    }
    assert counts["Postings in this file with status Open"] == 4
    assert counts["Postings in this file with status Closed"] == 2
    ranking = mapped["tech"].model
    assert {r.open_now for r in ranking.ranking if r.platform == "Salesforce"} == {4}
    assert all(
        p.status in ("open", "closed", "unknown") for p in content.postings
    )  # stored values untouched


@pytest.mark.asyncio
async def test_a_posting_title_carries_no_dash_as_punctuation(make_builder: Make) -> None:
    from exports.verify import docx_text

    content = account(n_postings=3, n_people=2)
    postings = list(content.postings)
    postings[0] = postings[0].model_copy(update={"title": "Manager – Salesforce Platforms"})
    postings[1] = postings[1].model_copy(update={"title": "Lead — CRM - EMEA"})
    people = [p.model_copy(update={"title": "Director – Sales Operations"}) for p in content.people]
    content = content.model_copy(update={"postings": tuple(postings), "people": tuple(people)})
    result, store = await run(make_builder, content)
    assert result.status == "complete", result.errors
    titles = {c.value for c in sheet_of(store, result, "jobs", "Jobs")["B"][1:]}
    assert "Manager, Salesforce Platforms" in titles and "Lead, CRM, EMEA" in titles
    people_titles = {c.value for c in sheet_of(store, result, "people", "People")["C"][1:]}
    assert people_titles == {"Director, Sales Operations"}
    for kind in ("evidence", "summary"):
        d = next(x for x in result.deliverables if x.kind == kind)
        text = docx_text(store.objects[d.object_key])
        assert "–" not in text and "—" not in text
    evidence = next(x for x in result.deliverables if x.kind == "evidence")
    assert "Manager, Salesforce Platforms" in docx_text(store.objects[evidence.object_key])


# ---- 7: a cell over Excel's limit never stops the five files --------------------------------


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


@pytest.mark.asyncio
async def test_an_over_long_cell_in_people_or_tech_is_cut_and_counted(make_builder: Make) -> None:
    """Review finding 15: a 33,000 character cell in a workbook without a Long text sheet raised
    out of the writer and lost all five files; a 40,000 character title stopped Word."""
    from exports.excel.common import CUT_NOTE
    from exports.readers.common import TITLE_MAX

    content = account(n_people=3, n_postings=3, n_detections=2)
    people = list(content.people)
    people[0] = people[0].model_copy(update={"title": "T" * 33_000})
    people[1] = people[1].model_copy(update={"title": "\U0001f680" * 20_000})  # 40,000 units
    people[2] = people[2].model_copy(update={"former_employers": ("F" * 33_000,)})
    postings = list(content.postings)
    postings[0] = postings[0].model_copy(update={"title": "P" * 40_500, "full_text_read": True})
    dets = list(content.detections)
    dets[0] = dets[0].model_copy(update={"technology": "D" * 34_000})
    stack = list(content.stack)
    stack[0] = stack[0].model_copy(update={"technology": "S" * 34_000})
    content = content.model_copy(
        update={
            "people": tuple(people),
            "postings": tuple(postings),
            "detections": tuple(dets),
            "stack": tuple(stack),
        }
    )
    result, store = await run(make_builder, content)
    assert result.status == "complete", result.errors

    # the people titles are cut at the title limit before they reach a cell; the over-long
    # technology name is cut at the cell limit and closed with the note
    def cut_cells(kind: str) -> int:
        d = next(x for x in result.deliverables if x.kind == kind)
        book = load_workbook(io.BytesIO(store.objects[d.object_key]))
        return sum(
            1
            for ws in book.worksheets
            for row in ws.iter_rows()
            for c in row
            if isinstance(c.value, str) and c.value.endswith(CUT_NOTE)
        )

    # the former employers cell is cut at Excel's limit with the closing note; a stack name,
    # being part of its row's key, is cut in the model (one-thousand characters) like a title
    assert cut_cells("people") == 1
    tech = sheet_of(store, result, "tech", "Confirmed stack")
    assert max(len(str(c.value or "")) for c in tech["C"]) <= TITLE_MAX
    assert result.manifest.long_text_parts >= 6  # titles, names and one cell cut
    jobs = sheet_of(store, result, "jobs", "Jobs")
    longest = max(len(str(c.value or "")) for c in jobs["B"][1:])
    assert longest <= TITLE_MAX
    assert CUT_NOTE.strip().startswith("[cut")


@pytest.mark.asyncio
async def test_excel_counts_utf16_units_so_a_description_of_emoji_splits_in_time(
    make_builder: Make,
) -> None:
    content = account(n_postings=3)
    postings = list(content.postings)
    postings[0] = postings[0].model_copy(
        update={"description": "\U0001f680" * 20_000 + " Salesforce end.", "full_text_read": True}
    )
    content = content.model_copy(update={"postings": tuple(postings)})
    result, store = await run(make_builder, content)
    assert result.status == "complete", result.errors
    book = load_workbook(
        io.BytesIO(
            store.objects[next(d for d in result.deliverables if d.kind == "jobs").object_key]
        )
    )
    for ws in book.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    assert _units(cell.value) <= 32_767, (ws.title, cell.coordinate)
    long_rows = [r for r in book["Long text"].iter_rows(min_row=2, values_only=True) if r[0]]
    assert long_rows  # the description continued on the Long text sheet


# ---- 8: data/releases/ in the workspace zip --------------------------------------------------


def _releases(n: int = 4) -> tuple[Any, ...]:
    from datetime import date

    from exports.content import ReleaseRow

    return tuple(
        ReleaseRow(
            kind=("posting", "page", "feed_item")[i % 3],
            url=f"https://alpha.example/releases/item-{i}",
            title=f"Synthetic release {i}: platform news",
            day=date(2026, 9, 1 + i),
            text=f"Synthetic text of release {i}.\nA second line.",
        )
        for i in range(n)
    )


def _zip_members(store: MemoryStore, result: BuildResult) -> dict[str, bytes]:
    import zipfile

    d = next(x for x in result.deliverables if x.kind == "workspace")
    with zipfile.ZipFile(io.BytesIO(store.objects[d.object_key])) as z:
        return {n: z.read(n) for n in z.namelist()}


@pytest.mark.asyncio
async def test_the_workspace_zip_carries_one_text_file_per_archived_copy(
    make_builder: Make,
) -> None:
    content = account().model_copy(update={"releases": _releases(4)})
    result, store = await run(make_builder, content)
    assert result.status == "complete", result.errors
    members = _zip_members(store, result)
    names = sorted(n for n in members if n.startswith("data/releases/"))
    assert len(names) == 4 and names == [n for n in members if n.startswith("data/releases/")]
    first = members[names[0]].decode()
    # the reference layout: title, url, date, source lines, a blank line, then the text
    assert first.splitlines()[:4] == [
        "title: Synthetic release 0: platform news",
        "url: https://alpha.example/releases/item-0",
        "date: 2026-09-01",
        "source: 6th agents",
    ]
    assert first.splitlines()[4] == "" and first.endswith("A second line.\n")
    assert names[0].startswith("data/releases/2026-09-01_synthetic-release-0-platform-news-")
    assert names[0].endswith(".md")
    restore = members["RESTORE.md"].decode()
    assert "releases/ (4 text files" in restore
    assert "ask your 6th agents contact" in restore and "6th agents rebuild" not in restore
    manifest = json.loads(members["MANIFEST.json"])
    assert {m["name"] for m in manifest["members"]} >= set(names)


@pytest.mark.asyncio
async def test_the_release_files_are_deterministic_and_stable_per_address(
    make_builder: Make,
) -> None:
    content = account().model_copy(update={"releases": _releases(5)})
    one, store_one = await run(make_builder, content)
    # another order in the snapshot, the same copies: the same names and the same bytes
    shuffled = content.model_copy(update={"releases": tuple(reversed(content.releases))})
    two, store_two = await run(make_builder, shuffled)
    a, b = _zip_members(store_one, one), _zip_members(store_two, two)
    assert sorted(a) == sorted(b) and all(a[n] == b[n] for n in a if n.startswith("data/"))
    # a newer copy of one address elsewhere in the list does not rename the others
    extra = _releases(6)
    three, store_three = await run(make_builder, content.model_copy(update={"releases": extra}))
    c = _zip_members(store_three, three)
    assert {n for n in a if n.startswith("data/releases/")} <= set(c)


@pytest.mark.asyncio
async def test_a_copy_a_client_rule_refuses_is_left_out_and_listed_not_fatal(
    make_builder: Make,
) -> None:
    from tests.exports.corpus import broker

    rows = list(_releases(4))
    rows[0] = rows[0].model_copy(update={"text": "Film credits roll after the talk."})
    rows[1] = rows[1].model_copy(update={"text": f"A page that names {broker('p_scrape')}."})
    rows[2] = rows[2].model_copy(update={"text": "Synthetic Other Account Inc is a rival."})
    content = account().model_copy(update={"releases": tuple(rows)})
    result, store = await run(make_builder, content)
    assert result.status == "complete", result.errors
    members = _zip_members(store, result)
    kept = {n: v.decode() for n, v in members.items() if n.startswith("data/releases/")}
    # a data tool's name in a copied page is masked, as in the pull log, and the copy stays
    assert len(kept) == 2 and all(broker("p_scrape") not in v for v in kept.values())
    assert any("A page that names" in v for v in kept.values())
    held = [b for b in result.manifest.withheld_blocks if b.startswith("workspace.releases:")]
    assert sorted(b.rsplit(":", 1)[1] for b in held) == ["leak", "third_party"]


@pytest.mark.asyncio
async def test_the_cap_keeps_the_newest_copies(make_builder: Make, cfg: Any) -> None:
    cap = cfg.rules.workspace.releases.max_files
    assert cap == 2000  # the reader's default and the configured cap agree
    rows = _releases(5)
    small = cfg.model_copy(
        update={
            "rules": cfg.rules.model_copy(
                update={
                    "workspace": cfg.rules.workspace.model_copy(
                        update={
                            "releases": cfg.rules.workspace.releases.model_copy(
                                update={"max_files": 2}
                            )
                        }
                    )
                }
            )
        }
    )
    store = MemoryStore()
    content = account().model_copy(update={"releases": rows})
    result = await make_builder(content, store, config=small).build_all(
        content.tenant_id, content.run_id
    )
    assert result.status == "complete", result.errors
    kept = [n for n in _zip_members(store, result) if n.startswith("data/releases/")]
    assert len(kept) == 2  # the first two of the reader's newest-first order
