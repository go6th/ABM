"""The delivered workbooks' sheets as tabs of the deep-dive tables (``replica.file_tabs``), the
formula values they show (``replica.workbook_formulas``), the file's own table standing in when
the stored table cannot be shown, and the LinkedIn links of the People tables (owner request
2026-10-27: "the table should be created in tabs the way the zip file has it; the LinkedIn
profile link is the main thing"). SYNTHETIC data only (the export test corpus and world)."""

from __future__ import annotations

import hashlib
import io
import json
from typing import Any

import pytest
from openpyxl import Workbook, load_workbook

from account_intelligence.grading.config import load_grading_config
from account_intelligence.replica import file_tabs as FT
from account_intelligence.replica import sheets as SH
from account_intelligence.replica.workbook_formulas import Evaluator, matcher
from exports.builder import map_snapshot
from exports.config import load_export_config
from exports.excel.common import expected_of
from exports.excel.jobs import plan_jobs, render_jobs
from exports.excel.people import plan_people, render_people
from exports.excel.tech import plan_tech, render_tech

CFG, GRADING = load_export_config(), load_grading_config()
RENDER = {"people": render_people, "jobs": render_jobs, "tech": render_tech}


def _books() -> dict[str, tuple[bytes, Any]]:
    """The three workbooks of the rich synthetic corpus, as the builder renders them."""
    from tests.exports.corpus import rich

    mapped, _ = map_snapshot(rich(), CFG, GRADING)
    return {
        k: (RENDER[k](mapped[k].model, CFG, f"Synthetic {k}")[0], mapped[k].model) for k in RENDER
    }


def _grid(data: bytes) -> dict[tuple[str, int, int], Any]:
    wb = load_workbook(io.BytesIO(data))
    return {
        (ws.title, c.row, c.column): c.value
        for ws in wb.worksheets
        for row in ws.iter_rows()
        for c in row
        if c.value is not None
    }


def test_the_web_tabs_compute_the_builders_formulas() -> None:
    """Every formula the builder writes evaluates to the value the builder computed in code (the
    file stores no cached value, so this is what the tab shows)."""
    from openpyxl.utils.cell import column_index_from_string, coordinate_from_string

    books = _books()
    plans = {
        "people": lambda m: plan_people(m, CFG)[1],
        "jobs": lambda m: plan_jobs(m, CFG),
        "tech": lambda m: plan_tech(m, CFG),
    }
    checked = 0
    for kind, (data, model) in books.items():
        ev = Evaluator(_grid(data))
        for (sheet, coord), want in expected_of(plans[kind](model)).items():
            col, row = coordinate_from_string(coord)
            got = ev.value(sheet, row, column_index_from_string(col))
            assert got == pytest.approx(want), (kind, sheet, coord)
            checked += 1
    assert checked > 50


def test_the_web_tabs_are_the_delivered_workbooks_sheets() -> None:
    books = _books()
    for kind, (data, model) in books.items():
        layout = getattr(CFG.rules.workbooks, kind)
        book = FT.read(data, layout.sheet)
        names = load_workbook(io.BytesIO(data)).sheetnames
        # every sheet, in the file's order, the configured one marked as the main table
        assert [t.name for t in book.tabs] == names
        assert [t.name for t in book.tabs if t.main] == [layout.sheet]
        assert len({t.key for t in book.tabs}) == len(names)
        main = book.tab(None)
        assert main is not None and main.table is not None
        assert list(main.table.header) == [c.header for c in layout.columns]
        assert len(main.table.rows) == len(model.rows)
    # the People List: each LinkedIn cell shows the file's text and opens the profile
    people = FT.read(books["people"][0], CFG.rules.workbooks.people.sheet)
    main = people.tab(None)
    assert main is not None and main.table is not None
    j = list(main.table.header).index("LinkedIn")
    model = books["people"][1]
    for cell, row in zip((r[j] for r in main.table.rows), model.rows, strict=True):
        assert cell.text == CFG.rules.sheet_texts.people.link_text
        assert cell.href == row.profile_url
    # the Summary sheet: its title line first, then its tables with computed counts
    summary = people.tab(FT.tab_key(CFG.rules.workbooks.people.summary_sheet))
    assert summary is not None and summary.blocks[0].kind == "text"
    tables = [b for b in summary.blocks if b.kind == "table"]
    assert tables and all(r[1].text != "" for t in tables for r in t.rows)
    # the Tech workbook opens on the Platform Ranking, whose rank cells are computed
    tech = FT.read(books["tech"][0], CFG.rules.workbooks.tech.sheet)
    ranking = tech.tabs[0]
    assert ranking.name == CFG.rules.workbooks.tech.ranking_sheet and not ranking.main
    table = ranking.table
    assert table is not None and table.rows
    assert {r[0].text for r in table.rows} <= {"A", "B", "C", ""}


def test_the_file_table_sorts_and_pages_like_a_sheet() -> None:
    data, model = _books()["people"]
    main = FT.read(data, CFG.rules.workbooks.people.sheet).tab(None)
    assert main is not None and main.table is not None
    live = SH.SHEETS["people"]
    q = FT.grid_query(main, live, None, None, 0, 5)
    page = FT.grid_page(main, q)
    assert page["from_file"] and page["total"] == len(model.rows) and len(page["rows"]) == 5
    assert [c["label"] for c in page["columns"]] == list(main.table.header)
    name = f"c{list(main.table.header).index('Name') + 1}"
    q = FT.grid_query(main, live, name, "desc", 0, 500)
    names = [r["cells"][name] for r in FT.grid_page(main, q)["rows"]]
    assert names == sorted(names, key=lambda t: t.casefold(), reverse=True)
    link = f"c{list(main.table.header).index('LinkedIn') + 1}"
    assert all(r["links"][link] for r in FT.grid_page(main, q)["rows"])
    with pytest.raises(SH.BadSheetQuery):
        FT.grid_query(main, live, "nope", None, 0, 5)


def test_criteria_follow_excel() -> None:
    assert matcher("*Sales*")("Salesforce, SAP") and not matcher("*Sales*")("SAP")
    assert matcher("open")("Open") and not matcher("open")("Opened")
    assert matcher("=~*x")("*x") and not matcher("=~*x")("ax")
    assert matcher(">=5")(7) and not matcher(">=5")(3) and not matcher(">=5")("text")
    assert matcher("<>Yes")("No") and not matcher("<>Yes")("yes")
    grid = {("S", 1, 1): 2, ("S", 2, 1): 4, ("S", 3, 1): "=SUM(A1:A2)/A1", ("S", 4, 1): "=A4+1"}
    ev = Evaluator(grid)
    assert ev.value("S", 3, 1) == 3
    assert ev.value("S", 4, 1) is None  # a cycle shows empty, never a wrong number
    assert Evaluator({("S", 1, 1): "=VLOOKUP(1,A2:B3,2)"}).value("S", 1, 1) is None


def test_an_uploaded_workbooks_link_cells_keep_their_address() -> None:
    """A People List uploaded as a file keeps each "Open profile" cell's LinkedIn address (the
    read-only reader drops hyperlinks; ``tabular.sheet_links`` reads them from the package)."""
    from research_files.parsers.tabular import linked, sheet_links

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "People"
    ws.append(["Name", "Title as reported", "LinkedIn", "Note"])
    ws.append(["Synthetic Person", "Director", "Open profile", "Open to work"])
    ws.cell(row=2, column=3).hyperlink = "https://www.linkedin.com/in/synthetic-person"
    ws.cell(row=2, column=4).hyperlink = "https://example.com/not-a-label"
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    links = sheet_links(buf)["People"]
    cells = linked(["Synthetic Person", "Director", "Open profile", "Open to work"], 2, links)
    assert cells[2] == "https://www.linkedin.com/in/synthetic-person"
    assert cells[3] == "https://example.com/not-a-label"  # "Open to work" reads as a link label
    assert sheet_links(io.BytesIO(b"not a zip")) == {}


# ---- the API ---------------------------------------------------------------------------------


async def _world() -> Any:
    import psycopg

    from tests.conftest import ADMIN_DSN
    from tests.exports.test_db import builder, seed_world

    w, payloads = await seed_world()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        c.execute(
            "UPDATE account_runs SET status = 'COMPLETED', finished_at = now() WHERE id = %s",
            (w.run,),
        )
    return w, payloads, builder(w, payloads)


async def _deliver_files(w: Any, payloads: Any, build: Any) -> Any:
    """The run's real workbooks stored as the builder stores them (manifest row counts
    included)."""
    import psycopg

    from tests.conftest import ADMIN_DSN

    content = await build.reader.read(str(w.tenant), str(w.run))
    mapped, _ = map_snapshot(content, CFG, GRADING)
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        for kind, render in RENDER.items():
            body, _ = render(mapped[kind].model, CFG, f"Synthetic {kind}")
            sha = hashlib.sha256(body).hexdigest()
            key = f"{w.tenant}/{w.account}/{w.run}/deliverables/{sha}.xlsx"
            payloads.objects[key] = body
            n = len(mapped[kind].model.rows)
            c.execute(
                "INSERT INTO deliverables (tenant_id, account_id, run_id, set_hash, kind, version,"
                " filename, sha256, size_bytes, object_key, manifest_json, content_hash,"
                " snapshot_hash, theme_hash, rules_hash, built_at, owner_user_id) VALUES"
                " (%s, %s, %s, %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(),"
                " coalesce((SELECT owner_user_id FROM accounts WHERE id = %s), 'test-owner'))"
                " ON CONFLICT DO NOTHING",
                (
                    w.tenant, w.account, w.run, sha, kind, f"{kind}.xlsx", sha,
                    len(body), key, json.dumps({"rows": n, "content_hash": sha}), sha, sha, sha,
                    sha, w.account,
                ),
            )  # fmt: skip
    return mapped


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_tables_show_every_sheet_of_the_delivered_workbooks_as_tabs() -> None:
    from tests.replica.test_file_sheets import _app, _get

    w, payloads, build = await _world()
    plane = _app(w, payloads, build)
    base = f"/v1/accounts/{w.domain}/sheets"
    # before a delivery: no tabs, and the live People table has its LinkedIn column
    live = (await _get(plane, f"{base}/people")).json()
    assert live["tabs"] == [] and live["from_file"] is False
    assert "LinkedIn" in [c["label"] for c in live["columns"]]
    await _deliver_files(w, payloads, build)
    for key, kind in FT.KINDS.items():
        r = await _get(plane, f"{base}/{key}")
        assert r.status_code == 200, (key, r.text)
        sheet = r.json()
        assert sheet["from_file"] is True and sheet["file_id"]
        labels = [t["label"] for t in sheet["tabs"]]
        layout = getattr(CFG.rules.workbooks, kind)
        assert layout.sheet in labels and [t["main"] for t in sheet["tabs"]].count(True) == 1
        for t in sheet["tabs"]:
            page = await _get(plane, f"{base}/{key}/tabs/{t['key']}")
            assert page.status_code == 200, (key, t, page.text)
            body = page.json()
            assert body["tab_label"] == t["label"] and body["tabs"] == sheet["tabs"]
    assert (await _get(plane, f"{base}/people/tabs/no-such-sheet")).status_code == 404
    assert (await _get(plane, f"{base}/signals/tabs/summary")).status_code == 404


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_stale_stored_table_shows_the_files_own_table() -> None:
    """When the delivered run's stored table cannot be shown (here: it was stored under an
    earlier column set, as runs delivered before a column change are), the People sheet is the
    delivered file's table, links included, never the live research table with other columns;
    the counts and exports are the same rows."""
    import psycopg

    from tests.conftest import ADMIN_DSN
    from tests.replica.test_file_sheets import _app, _get

    w, payloads, build = await _world()
    plane = _app(w, payloads, build)
    mapped = await _deliver_files(w, payloads, build)
    base = f"/v1/accounts/{w.domain}/sheets/people"
    assert (await _get(plane, base)).json()["from_file"] is True  # stored table in use
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        c.execute(
            "UPDATE view_rows SET cells = cells - 'note'"
            " WHERE run_id = %s AND table_kind = 'people'",
            (w.run,),
        )
    sheet = (await _get(plane, base)).json()
    headers = [c.header for c in CFG.rules.workbooks.people.columns]
    assert sheet["from_file"] is True and sheet["tabs"]
    assert [c["label"] for c in sheet["columns"]] == headers
    assert sheet["total"] == len(mapped["people"].model.rows)
    link = f"c{headers.index('LinkedIn') + 1}"
    for got, row in zip(sheet["rows"], mapped["people"].model.rows, strict=False):
        assert got["links"].get(link) == row.profile_url
        assert got["cells"][link] == CFG.rules.sheet_texts.people.link_text
    ws = (await _get(plane, f"/v1/accounts/{w.domain}/workspace")).json()
    assert ws["counts"]["people"] == sheet["total"]
    csv_body = (await _get(plane, f"{base}/csv")).content.decode("utf-8-sig")
    assert csv_body.splitlines()[0].split(",")[1:3] == ["No.", "Name"]
    xlsx = await _get(plane, f"{base}/xlsx")
    assert xlsx.status_code == 200, xlsx.text


@pytest.mark.requires_postgres
def test_a_people_fact_that_is_only_a_title_is_not_a_person() -> None:
    """The People table leaves out stored people facts whose value is only a job title (the
    owner's exported People list showed "Vice President of Technology Product Management" as a
    name); a title followed by a name, or a name, stays."""
    import psycopg

    from account_intelligence.replica.workspace import title_only
    from tests.conftest import ADMIN_DSN

    values = {
        "Vice President of Technology Product Management": True,
        "Senior VP of Product and Strategy": True,
        "Principal Product Manager, Synthetic Unit by Synthetic Co": True,
        "Synthetic Person, President and Chief Executive Officer": False,
        "Chief Information Officer, Synthetic Person": False,
        "Head of Data, Synthetic Q. Person": False,
        "Synthetic Person": False,
    }
    with psycopg.connect(ADMIN_DSN) as c:
        for value, title in values.items():
            sql = f"SELECT {title_only('x.v')} FROM (SELECT CAST(%s AS text) AS v) x"
            got = c.execute(sql, (value,)).fetchone()
            assert got is not None and got[0] is title, value
