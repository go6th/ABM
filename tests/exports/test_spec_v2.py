"""The owner's output specification for the three workbooks (master prompt v2, 2026-10-24,
sections 7.3, 9.3 to 9.5 and 12): the People List labels and Summary tables, the Job Postings
columns and matrix, the Platform Ranking, the masking of out-of-scope platforms in every file,
and the third-party scan over every file. Every formula cell is registered with its value."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml
from openpyxl import load_workbook

from account_intelligence.config import CONFIG_DIR
from account_intelligence.grading.config import GradingConfig
from exports.builder import ExportBuilder
from exports.config import ExportConfig, load_export_config
from exports.content import DetectionRow, PeopleCounts, PersonRow, PostingRow, SourceRow
from exports.errors import ConfigError
from exports.excel.jobs import expected_jobs
from exports.excel.people import expected_people
from exports.excel.tech import expected_tech
from exports.models import BuildResult
from exports.readers.common import MapContext
from exports.readers.jobs import map_jobs
from exports.readers.people import map_people
from exports.readers.tech import map_tech
from jobs.window import months_before
from tests.exports.corpus import AS_OF, account
from tests.fakes.exports import MemoryStore

pytestmark = pytest.mark.asyncio
Make = Callable[..., ExportBuilder]


async def _build(make_builder: Make, content: Any) -> tuple[BuildResult, dict[str, bytes]]:
    store = MemoryStore()
    result = await make_builder(content, store).build_all(content.tenant_id, content.run_id)
    files = {}
    for d in result.deliverables:
        files[d.kind] = store.objects[d.object_key]
    return result, files


def _texts(data: bytes) -> str:
    """Every text member of a package, nested packages included (what a reader could see)."""
    out = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            body = z.read(name)
            if name.endswith((".docx", ".xlsx")):
                out.append(_texts(body))
            else:
                out.append(body.decode("utf-8", "ignore"))
    return "\n".join(out)


def _formula_cells(data: bytes) -> dict[tuple[str, str], str]:
    wb = load_workbook(io.BytesIO(data))
    return {
        (ws.title, c.coordinate): str(c.value)
        for ws in wb.worksheets
        for row in ws.iter_rows()
        for c in row
        if c.data_type == "f"
    }


def _posting(src: Any, key: str, text: str, **over: Any) -> PostingRow:
    s = src.add(f"jobs/{key}", kind="primary")
    values: dict[str, Any] = {
        "posting_key": key,
        "title": f"Synthetic role {key}",
        "url": s.url,
        "status": "open",
        "posting_class": "build",
        "platforms": ("Salesforce",),
        "platform_sentences": (text,),
        "description": text,
        "posted_at": date(2026, 6, 1),
        "first_seen": date(2026, 6, 2),
        "full_text_read": True,
    }
    values.update(over)
    return PostingRow(**values)


class _Registry:
    """Adds sources to an existing content's registry, continuing its codes."""

    def __init__(self, content: Any) -> None:
        self.rows = list(content.sources)
        self.host = content.domain

    def add(self, path: str, kind: str = "primary", host: str | None = None) -> SourceRow:
        row = SourceRow(
            code=f"S-{len(self.rows) + 1:04d}",
            url=f"https://{host or self.host}/{path}",
            published=AS_OF,
            kind=kind,
        )
        self.rows.append(row)
        return row


# ---- 4. masking ------------------------------------------------------------------------------


async def test_out_of_scope_platforms_are_masked_in_every_file(
    make_builder: Make, cfg: ExportConfig
) -> None:
    c = account()
    src = _Registry(c)
    never = cfg.scope.never_report
    postings = (
        *c.postings,
        _posting(src, "mask-1", f"Administers Salesforce and {never[0]} daily."),
    )
    det = c.detections[0].model_copy(update={"technology": never[2], "id": "mask-det"})
    content = c.model_copy(
        update={
            "postings": postings,
            "detections": (*c.detections, det),
            "sources": tuple(src.rows),
        }
    )
    result, files = await _build(make_builder, content)
    assert result.status == "complete", result.errors
    assert result.manifest is not None
    # the description, the platform sentence and the detection technology at least; the
    # evidence file repeats the sentence
    assert result.manifest.masked_terms >= 3
    for kind, body in files.items():
        text = _texts(body).casefold()
        for name in never:
            assert name.casefold() not in text, (kind, name)
    # the mask itself is on the sheets that carried the names
    assert cfg.scope.mask in _texts(files["jobs"]) and cfg.scope.mask in _texts(files["tech"])


def test_never_report_names_are_never_platforms_or_lane_labels(cfg: ExportConfig) -> None:
    names = {n.casefold() for n in cfg.scope.never_report}
    for e in cfg.platforms:
        assert e.name.casefold() not in names
        for n in cfg.scope.never_report:
            assert not e.names(n)
    for lane_id in cfg.scope.lanes:
        label = cfg.lane_label(lane_id) or ""
        assert not any(n in label.casefold() for n in names), label
    for label in cfg.rules.labels.people_lane.values():  # the People List's own lane wording
        assert not any(n in label.casefold() for n in names), label


def test_platform_dictionary_refuses_a_bad_pattern_or_a_masked_name(tmp_path: Path) -> None:
    for name in ("export_theme.yaml", "export_rules.yaml", "lanes.yaml", "output_spec.yaml"):
        (tmp_path / name).write_bytes((CONFIG_DIR / name).read_bytes())
    doc = yaml.safe_load((CONFIG_DIR / "platform_dictionary.yaml").read_text())
    bad = {**doc, "platforms": [{**doc["platforms"][0], "pattern": "("}]}
    (tmp_path / "platform_dictionary.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(ConfigError, match="does not compile"):
        load_export_config(tmp_path)
    scope = yaml.safe_load((CONFIG_DIR / "skill" / "scope.yaml").read_text())
    masked = scope["never_report"]["value"][0]
    bad = {**doc, "platforms": [{**doc["platforms"][0], "name": masked}]}
    (tmp_path / "platform_dictionary.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(ConfigError, match="never ranked"):
        load_export_config(tmp_path)
    for e in load_export_config().platforms:
        assert e.regex.pattern == e.pattern  # every pattern compiles


# ---- 1. People List ----------------------------------------------------------------------------


def test_people_labels_seniority_lane_and_found_by(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    c = account(n_people=0)
    src = _Registry(c)
    cases = (
        ("c level", "salesforce, data ai", "A", "title_search", "C-level, EVP and SVP", True),
        ("vp", "", "B", "association", "VP", True),
        ("senior manager", "data_ai", "B", "mention", "Senior Manager", True),
        ("manager", "veeva", "C", "title_search", "Manager", False),
        ("analyst", "", "C", "title_search", "Lead, owner, specialist or developer", False),
    )
    people = []
    for i, (level, lanes, prio, origin, _label, _senior) in enumerate(cases):
        s = src.add(f"people/p-{i}", kind="secondary")
        people.append(
            PersonRow(
                id=f"pk-{i}",
                name=f"Synthetic Person {i}",
                title=f"Synthetic title {i}",
                level=level,
                lanes=lanes,
                priority=prio,
                profile_url=s.url,
                source_code=s.code,
                origin=origin,
                former_employers=("Synthetic Integrator",) if i == 0 else None,
                second_profile_url=f"https://{c.domain}/people/alt-{i}" if i == 1 else None,
                location="Synthetic City, Synthetic Country" if i == 0 else None,
            )
        )
    # stored in reverse: the list orders them by level, priority, lane and name
    people.reverse()
    content = c.model_copy(update={"people": tuple(people), "sources": tuple(src.rows)})
    ctx = MapContext(content, cfg, grading)
    model = map_people(ctx).model
    assert model is not None and not ctx.violations
    labels = cfg.rules.labels
    for row, (_, _lanes, _, origin, label, senior) in zip(model.rows, cases, strict=True):
        assert row.level == label and row.meets_seniority is senior
        assert row.found_by == labels.found_by[origin]
    assert [r.number for r in model.rows] == [1, 2, 3, 4, 5]
    assert [r.row_id for r in model.rows] == [f"pk-{i}" for i in range(5)]
    assert model.rows[0].lanes == labels.people_lane["salesforce"] == "Salesforce and commercial"
    # the second lane is kept, in the note, in the People List's wording
    assert labels.people_lane["data_ai"] in model.rows[0].note
    assert model.rows[1].lanes == labels.lane_none
    assert model.rows[0].location == "Synthetic City, Synthetic Country"
    assert model.rows[1].location == ""
    assert model.rows[0].former_employers == "Synthetic Integrator"
    assert model.rows[1].former_employers == labels.not_pulled
    assert model.rows[1].second_profile_url and model.rows[0].second_profile_url is None
    assert model.senior_count == 3
    by_label = {x.label: x for x in model.levels}
    assert list(by_label) == list(labels.people_level_order)
    assert by_label["C-level, EVP and SVP"].people == 1
    assert by_label["C-level, EVP and SVP"].priority_a == 1
    assert sum(x.people for x in model.levels) == 5
    lanes_table = {x.label: x for x in model.lanes_table}
    # two lane ids that share a People List label are one line of the Lane table
    assert len(lanes_table) == len(model.lanes_table)
    assert lanes_table[labels.people_lane["salesforce"]].a == 1
    assert (
        lanes_table[labels.people_lane["data_ai"]].b == 1
    )  # the senior manager; the manager is not senior
    assert lanes_table[labels.lane_none].b == 1 and lanes_table[labels.lane_none].total == 1
    assert cfg.scope.seniority_rule in model.seniority_text
    assert model.seniority_text.endswith("Filter Meets seniority rule to Yes for the target list.")
    # the Summary sheet's title and paragraphs in the reference's order, from stored counts
    assert model.title_line == f"{c.account_name} people list, deduplicated"
    what = model.paragraphs[0]
    assert what.startswith(f"This is the single people list for the {c.account_name} account.")
    assert "(3 people on this list)" in what  # title search: rows 0, 3 and 4
    assert "technology association searches (1 person on this list)" in what
    assert "people named in research (1 person on this list)" in what
    assert "Pulled by 6th agents on " in what
    assert model.paragraphs[-2] == model.seniority_text
    assert model.paragraphs[-1] == model.judgment_note
    registered = {n.key for n in ctx.registry.items()}
    assert {"people.listed", "people.withheld", "people.senior", "people.level.00"} <= registered


async def test_people_summary_names_the_excluded_with_reason_and_formulas_are_registered(
    make_builder: Make, cfg: ExportConfig
) -> None:
    c = account()
    counts = c.people_counts
    assert counts is not None
    content = c.model_copy(
        update={
            "people_counts": PeopleCounts(
                summary=counts.summary,
                sentences=counts.sentences,
                in_scope=counts.in_scope,
                excluded=(
                    ("Synthetic Excluded Person", "security", "Director, Information Security"),
                ),
            )
        }
    )
    result, files = await _build(make_builder, content)
    assert result.status == "complete", result.errors
    wb = load_workbook(io.BytesIO(files["people"]))
    assert wb.sheetnames[:2] == ["Summary", "People"]  # the reference's sheet order
    people = [[c.value for c in row] for row in wb["People"].iter_rows()]
    assert people[0][:13] == [
        "No.",
        "Name",
        "Title as reported",
        "Level",
        "Meets seniority rule",
        "Lane",
        "Priority",
        "Former SI and consulting employers (firm and years)",
        "Found by",
        "LinkedIn",
        "Second profile",
        "Location",
        "Note",
    ]
    assert people[0][-1] == "Source" and wb["People"].freeze_panes == "C2"
    link = wb["People"].cell(row=2, column=10)
    assert link.value == "Open profile" and str(link.hyperlink.target).startswith("https://")
    assert "Synthetic Excluded Person" not in str(people)
    summary = [[c.value for c in row] for row in wb["Summary"].iter_rows()]
    assert summary[0][0] == f"{c.account_name} people list, deduplicated"
    text = " ".join(str(v) for row in summary for v in row if v is not None)
    assert (
        "Security people are excluded at the account owner's instruction (1 row): "
        "Synthetic Excluded Person (Director, Information Security)."
    ) in text
    assert cfg.scope.seniority_rule in text and "self reported" in text
    assert "Rows removed before this list: " in text and "1 row merged as duplicates" in text
    labels_col = [row[0] for row in summary]
    assert labels_col.index("Level") < labels_col.index("Lane, senior people only")
    level_at = labels_col.index("Level")
    assert labels_col[level_at + 1 : level_at + 10] == list(cfg.rules.labels.people_level_order)
    assert labels_col[level_at + 10 : level_at + 12] == ["Total", "Meets seniority rule"]
    # the merged person stays guarded: never in any file
    for body in files.values():
        assert "Synthetic Merged Person" not in _texts(body)
    mapped = make_builder(content).assemble(content).mapped["people"].model
    assert mapped is not None
    expected = expected_people(mapped, cfg)
    formulas = _formula_cells(files["people"])
    assert set(expected) == set(formulas), set(expected) ^ set(formulas)
    assert all(sheet == "Summary" for sheet, _ in expected)


# ---- 2. Job Postings ---------------------------------------------------------------------------


def test_jobs_labels_entity_and_source(cfg: ExportConfig, grading: GradingConfig) -> None:
    c = account(n_postings=0).model_copy(update={"careers_host": "jobs.alpha-careers.example"})
    src = _Registry(c)
    postings = (
        _posting(src, "a", "Builds on Salesforce.", posting_class="build"),
        _posting(
            src,
            "b",
            "Salesforce named in the title only.",
            posting_class=None,
            full_text_read=False,
        ),
        _posting(
            src,
            "c",
            "A role with no lane platform in the text.",
            posting_class=None,
            platforms=("Workday",),
            platform_sentences=("Works in Workday.",),
            status="closed",
            posted_at=None,
            origin="live_board",
        ),
        _posting(src, "d", "Salesforce admin.", posting_class="admin_owner", origin="live_board"),
    )
    s_sub = src.add("jobs/sub", host=f"careers.{c.domain}")
    s_car = src.add("jobs/car", host="jobs.alpha-careers.example")
    s_other = src.add("jobs/other", host="boards.example", kind="aggregator")
    postings += (
        postings[0].model_copy(update={"posting_key": "e", "url": s_sub.url}),
        postings[0].model_copy(update={"posting_key": "f", "url": s_car.url}),
        postings[0].model_copy(update={"posting_key": "g", "url": s_other.url}),
    )
    content = c.model_copy(update={"postings": postings, "sources": tuple(src.rows)})
    ctx = MapContext(content, cfg, grading)
    model = map_jobs(ctx).model
    assert model is not None and not ctx.violations, ctx.violations
    labels = cfg.rules.labels
    rows = {r.row_id: r for r in model.rows}
    assert rows["a"].posting_class == "Build role"
    assert rows["b"].posting_class == "Not read (title only)" and rows["b"].description == ""
    assert rows["c"].posting_class == "No lane platform named"
    assert rows["c"].status == "Open or closed in the last 12 months"
    assert rows["c"].posted_or_seen == date(2026, 6, 2)  # first seen when not posted
    assert rows["a"].source_type == labels.posting_source["feed_read"]
    assert rows["b"].source_type == labels.posting_source["feed_title_only"]
    assert rows["d"].source_type == labels.posting_source["live_board_read"]
    for key in ("a", "e", "f"):
        assert rows[key].entity == c.account_name, key
    assert rows["g"].entity == "boards.example" + labels.entity_to_confirm_suffix
    matrix = {m.platform: m for m in model.matrix}
    assert matrix["Salesforce"].rows == 6 and matrix["Workday"].rows == 1
    assert matrix["All rows"].rows == 7
    build_index = model.class_labels.index("Build role")
    assert matrix["Salesforce"].by_class[build_index] == 4
    assert matrix["Workday"].closed == 1 and matrix["Workday"].open == 0
    assert matrix["All rows"].open == 6 and matrix["All rows"].closed == 1
    assert {n.key for n in ctx.registry.items()} >= {"jobs.matrix.00", "jobs.matrix.00.00"}


def test_jobs_reference_columns(cfg: ExportConfig, grading: GradingConfig) -> None:
    """The reference file's columns: the requisition the posting prints, the stored location,
    the sentence the reading quoted (read postings only) and the full description apart."""
    c = account(n_postings=0)
    src = _Registry(c)
    text = "Job ID\n:   R-557322\n\nCategory\n: IT\n\nYou administer Salesforce for the sales team."
    postings = (
        _posting(
            src,
            "a",
            text,
            location="Heidelberg, Germany",
            class_sentence="You administer Salesforce for the sales team.",
        ),
        _posting(
            src,
            "b",
            "Requisition ID 1234 uses Salesforce.",
            full_text_read=False,
            class_sentence="uses Salesforce.",
        ),
        _posting(src, "c", "No job number printed here; works in Salesforce."),
    )
    content = c.model_copy(update={"postings": postings, "sources": tuple(src.rows)})
    model = map_jobs(MapContext(content, cfg, grading)).model
    assert model is not None
    rows = {r.row_id: r for r in model.rows}
    assert rows["a"].requisition == "R-557322" and rows["a"].location == "Heidelberg, Germany"
    assert rows["a"].read_text == "You administer Salesforce for the sales team."
    assert rows["a"].description.startswith("Job ID")
    # not read in full: no quoted sentence and no description, the printed ID still shown
    assert rows["b"].requisition == "1234" and rows["b"].read_text == ""
    assert rows["b"].description == ""
    assert rows["c"].requisition == "" and rows["c"].location == ""
    # the Summary sheet's title line and paragraphs
    assert model.summary_title == (
        f"{c.account_name} platform job postings, last 12 months"
        " (first seen 30 September 2025 to 30 September 2026)"
    )
    joined = " ".join(model.summary_notes)
    assert "6th agents" in joined and "entity to confirm" in joined
    for label in cfg.rules.labels.posting_class.values():
        assert label in joined, label
    headers = {col.key: col.header for col in cfg.rules.workbooks.jobs.columns}
    for key in ("requisition", "posted_or_seen", "read_text", "platform_sentence", "description"):
        assert headers[key] in joined, key
    for key in ("source_type", "ref", "source_code"):
        assert headers[key] in joined, key


async def test_jobs_sheet_and_matrix_formulas_are_registered(
    make_builder: Make, cfg: ExportConfig
) -> None:
    content = account()
    result, files = await _build(make_builder, content)
    assert result.status == "complete", result.errors
    wb = load_workbook(io.BytesIO(files["jobs"]))
    book = cfg.rules.workbooks.jobs
    # the reference file's order: Summary first, then Jobs; Counts and Long text after
    order = [book.summary_sheet, book.sheet, book.counts_sheet, book.long_text_sheet]
    assert wb.sheetnames == order
    headers = [c.value for c in wb["Jobs"][1]]
    assert headers == [c.header for c in book.columns]
    assert headers[:3] == ["Requisition", "Title", "Platforms named"]
    mapped = make_builder(content).assemble(content).mapped["jobs"].model
    assert mapped is not None
    summary = wb[book.summary_sheet]
    assert summary["A1"].value == mapped.summary_title
    assert summary["A1"].value.startswith(f"{content.account_name} platform job postings")
    assert [summary.cell(row=3 + i, column=1).value for i in range(len(mapped.summary_notes))] == (
        list(mapped.summary_notes)
    )
    texts = cfg.rules.sheet_texts.jobs
    header_row = 3 + len(mapped.summary_notes) + 1
    matrix_header = [c.value for c in summary[header_row]][:4]
    assert matrix_header == [texts.matrix_heading, texts.total, texts.closed, texts.open]
    expected = expected_jobs(mapped, cfg)
    formulas = _formula_cells(files["jobs"])
    assert set(expected) == set(formulas), set(expected) ^ set(formulas)
    assert {sheet for sheet, _ in expected} == {book.summary_sheet, book.counts_sheet}


# ---- 3. Platform Ranking -----------------------------------------------------------------------


def _ranking_content() -> Any:
    c = account(n_postings=0, n_detections=0)
    src = _Registry(c)
    postings = []
    # Salesforce named 6 times (A), ServiceNow 3 times (B), SAP twice (C), Workday once, and
    # one old posting outside the window naming Tableau twice over
    for i in range(6):
        postings.append(_posting(src, f"sf-{i}", "Salesforce admin work.", full_text_read=i < 3))
    for i in range(3):
        postings.append(_posting(src, f"sn-{i}", "ServiceNow ITSM work.", status="closed"))
    for i in range(2):
        postings.append(_posting(src, f"sap-{i}", "SAP S/4HANA work.", full_text_read=False))
    postings.append(_posting(src, "wd", "Workday payroll.", posted_at=None, first_seen=None))
    postings.append(_posting(src, "old", "Tableau Tableau.", posted_at=date(2024, 1, 1)))
    return c.model_copy(update={"postings": tuple(postings), "sources": tuple(src.rows)})


def test_platform_ranking_counts_rank_and_window(cfg: ExportConfig, grading: GradingConfig) -> None:
    content = _ranking_content()
    ctx = MapContext(content, cfg, grading)
    model = map_tech(ctx).model
    assert model is not None and not ctx.violations
    assert months_before(AS_OF, 12) == date(2025, 9, 30)
    assert model.postings_in_window == 12 and model.base == 12
    ranked = [(r.platform, r.count, r.rank) for r in model.ranking]
    assert ranked == [("Salesforce", 6, "A"), ("ServiceNow", 3, "B"), ("SAP", 2, "C")]
    assert [r.platform for r in model.seen_once] == ["Workday"]
    assert model.rank_counts == (("A", 1), ("B", 1), ("C", 1))
    sf, sn, sap = model.ranking
    assert sf.share == 0.5 and sf.open_now == 6 and sf.last_seen == "2026-06-01"
    assert sf.read_checked == "Yes" and sap.read_checked == "No"
    assert sf.in_stack == cfg.rules.labels.not_pulled  # the run has no detections
    assert sn.lane == cfg.rules.labels.lane["servicenow"]
    assert model.seen_once[0].lane == cfg.rules.labels.lane_outside
    assert "12 months" in model.base_line and model.title.startswith(content.account_name)
    registered = {n.key: n.value for n in ctx.registry.items()}
    assert registered["tech.ranking.base"] == 12 and registered["tech.ranking.rank.A"] == 1


def test_in_confirmed_stack_follows_the_detections(
    cfg: ExportConfig, grading: GradingConfig
) -> None:
    content = _ranking_content()
    s = _Registry(content)
    d = s.add("tech/d", kind="aggregator")
    det = DetectionRow(
        id="det-1",
        technology="Salesforce Service Cloud",
        first_seen=None,
        last_seen=None,
        source_url=d.url,
        source_code=None,
    )
    content = content.model_copy(update={"detections": (det,), "sources": tuple(s.rows)})
    model = map_tech(MapContext(content, cfg, grading)).model
    assert model is not None
    by = {r.platform: r for r in model.ranking}
    assert by["Salesforce"].in_stack == "Yes" and by["ServiceNow"].in_stack == "No"


async def test_ranking_sheet_is_first_and_its_formulas_are_registered(
    make_builder: Make, cfg: ExportConfig
) -> None:
    content = _ranking_content()
    result, files = await _build(make_builder, content)
    assert result.status == "complete", result.errors
    wb = load_workbook(io.BytesIO(files["tech"]))
    book = cfg.rules.workbooks.tech
    assert wb.sheetnames == ["Platform Ranking", book.sheet, "Platform teams"]
    ws = wb["Platform Ranking"]
    # the reference layout: title, base line with the base in the share column, four rule
    # paragraphs, a gap, the header on row 8 and the first platform on row 9
    title = f"{content.account_name}: platforms seen in job postings, ranked A, B, C"
    assert ws["A1"].value == title
    assert ws["A2"].value.startswith("Postings in the last 12 months") and ws["F2"].value == 12
    assert [c.value for c in ws[8]] == [c.header for c in book.ranking_columns]
    assert ws.freeze_panes == "A9" and ws.auto_filter.ref == "A8:J11"
    assert ws["A9"].value.startswith("=IF(") and ws["F9"].value == "=IF($F$2=0,0,E9/$F$2)"
    assert ws["B9"].value == "Salesforce" and ws["B11"].value == "SAP"
    assert ws["B14"].value == '=COUNTIF($A$9:$A$11,"A")' and ws["A14"].value == "Rank A"
    text = " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value is not None)
    for note in cfg.rules.sheet_texts.tech.__dict__.values():
        if isinstance(note, str) and note.endswith(".") and "{" not in note:
            assert note in text, note
    assert "Seen once (not ranked)" in text and "Workday" in text
    mapped = make_builder(content).assemble(content).mapped["tech"].model
    assert mapped is not None
    expected = expected_tech(mapped, cfg)
    formulas = _formula_cells(files["tech"])
    assert set(expected) == set(formulas), set(expected) ^ set(formulas)
    assert expected[("Platform Ranking", "A9")] == "A"
    assert expected[("Platform Ranking", "F9")] == 0.5
    # the seen-once block: platform, category and lane only, under its heading
    assert ws["A18"].value == "Seen once (not ranked)" and ws["B19"].value == "Workday"
    assert ws["E19"].value is None
    stack = wb[book.sheet]
    assert stack["A1"].value.startswith(content.account_name)
    assert [c.value for c in stack[4]] == [c.header for c in book.columns]
    teams = wb["Platform teams"]
    assert teams["A2"].value == cfg.rules.none_found.teams
    assert [c.value for c in teams[4]] == [c.header for c in book.teams_columns]
    assert teams.max_row == 4


# ---- 5. third-party scan -----------------------------------------------------------------------


async def test_third_party_scan_groups_hosts_and_fails_on_credits(
    make_builder: Make, cfg: ExportConfig
) -> None:
    c = account()
    src = _Registry(c)
    s_agg = src.add("board/1", host="boards.example", kind="aggregator")
    s_other = src.add("news/1", host="press.example", kind="secondary")
    postings = (
        *c.postings,
        _posting(src, "agg", "Salesforce role on a board.").model_copy(update={"url": s_agg.url}),
        _posting(src, "oth", "Salesforce role in the press.").model_copy(
            update={"url": s_other.url}
        ),
    )
    content = c.model_copy(update={"postings": postings, "sources": tuple(src.rows)})
    result, _files = await _build(make_builder, content)
    assert result.status == "complete", result.errors
    groups = result.manifest.third_party_domains
    assert groups["data_tools"] == ()
    assert c.domain in groups["account_and_press"]
    assert groups["aggregators"] == ("boards.example",)
    assert "press.example" in groups["other"]
    assert "jobs.alpha" not in str(groups)
    # the word "credits" in any client file fails the set
    first = c.postings[0].model_copy(update={"description": "Earn credits here."})
    result, _ = await _build(
        make_builder, c.model_copy(update={"postings": (first, *c.postings[1:])})
    )
    assert result.status == "failed" and any("credits" in e for e in result.errors)


async def test_a_data_tool_host_fails_the_build(make_builder: Make, cfg: ExportConfig) -> None:
    from exports.config import provider_hosts

    host = next(h for h in provider_hosts() if "parallel" in h)
    c = account()
    src = _Registry(c)
    s = src.add("jobs/x", host=host)
    postings = (
        *c.postings,
        _posting(src, "tool", "Salesforce role.").model_copy(update={"url": s.url}),
    )
    result, _ = await _build(
        make_builder, c.model_copy(update={"postings": postings, "sources": tuple(src.rows)})
    )
    assert result.status == "failed"
    assert any("data_tool_host" in e for e in result.errors), result.errors
