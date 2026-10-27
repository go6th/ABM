"""The Account workspace screen and its deliverable files.

* ``GET /v1/accounts/{domain}/workspace``: narrative, key findings, what changed (after a refresh
  only), deep-dive counts and files.
* ``GET /v1/accounts/{domain}/deliverables``: the newest version of each deliverable.
* ``GET /v1/accounts/{domain}/deliverables/download?ids=a,b``: one id downloads that file as
  itself; several (or ``all``) download one zip. Every download is an audited export. A file of
  another account or tenant is simply not found.
"""

from __future__ import annotations

import asyncio
import io
import re
import time
import zipfile
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import text

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import file_tabs as FT
from account_intelligence.replica import sheets as SH
from account_intelligence.replica import workspace as W
from agents.errors import GuardViolation
from agents.naming import sanitize_client_value
from obs.logging import logger
from tenancy.storage import PathRefused, require_owned

log = logger(__name__)
router = APIRouter(prefix="/accounts", tags=["accounts"])
register(
    "GET", "/v1/accounts/{domain}/workspace", tenant_route("read_public", "read", step_up=False)
)
register(
    "GET",
    "/v1/accounts/{domain}/file-analysis",
    tenant_route("read_public", "read", step_up=False),
)
register(
    "GET", "/v1/accounts/{domain}/deliverables", tenant_route("read_public", "read", step_up=False)
)
register(
    "GET",
    "/v1/accounts/{domain}/sheets/{sheet}",
    tenant_route("read_public", "read", step_up=False),
)
register(
    "GET",
    "/v1/accounts/{domain}/sheets/{sheet}/tabs/{tab}",
    tenant_route("read_public", "read", step_up=False),
)
register(
    "GET",
    "/v1/accounts/{domain}/sheets/{sheet}/xlsx",
    tenant_route("export_files", "export", step_up=False),
)
register(
    "GET",
    "/v1/accounts/{domain}/sheets/{sheet}/csv",
    tenant_route("export_files", "export", step_up=False),
)
register(
    "GET",
    "/v1/accounts/{domain}/deliverables/download",
    tenant_route("export_files", "export", step_up=False),
)

DOMAIN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$"
MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "zip": "application/zip",
}


async def account_id_of(session: Any, domain: str) -> UUID:
    """The account of this tenant with that domain, or 404 (another tenant's looks the same)."""
    if not re.fullmatch(DOMAIN, domain):
        raise HTTPException(404, "not found")
    found = await session.scalar(text("SELECT id FROM accounts WHERE domain = :d"), {"d": domain})
    if found is None:
        raise HTTPException(404, "not found")
    return UUID(str(found))


async def _delivered_tables(request: Request, ident: Identity, account_id: UUID) -> None:
    """Materialise the client tables of the account's delivered run, once per run
    (``views.materialize``), so the deep-dive sheets can show the files' own tables. A run that
    cannot be materialised leaves the live sheets in place; the failure is logged, never shown."""
    from account_intelligence.api.routes.accounts import materialized

    async with tenant_txn(ident.tenant_id) as s:
        run = await SH.delivered_run(s, account_id)
    if run is None:
        return
    try:
        await materialized(request, ident.tenant_id, run)
    except Exception as error:  # noqa: BLE001 - the live sheets stand in for the file tables
        log.warning("sheet_tables_unavailable", extra={"error": type(error).__name__})


async def _file_run(request: Request, ident: Identity, account_id: UUID, sheet: str) -> UUID | None:
    """The delivered run a file-shaped sheet reads (``sheets.file_run``), materialised first."""
    if sheet not in SH.FILE_TABLES:
        return None
    await _delivered_tables(request, ident, account_id)
    async with tenant_txn(ident.tenant_id) as s:
        return await SH.file_run(s, account_id, sheet)


async def _book(
    request: Request,
    ident: Identity,
    account_id: UUID,
    sheet: str,
    *,
    builder_only: bool = False,
) -> tuple[FT.Book, FT.Delivered] | None:
    """The delivered workbook of a sheet, read back as tabs (``replica.file_tabs``), or None
    before a delivery or when the file cannot be read. ``builder_only``: only when the file can
    stand in for the stored table (``_file_main``), so no other file is read for nothing."""
    if sheet not in FT.KINDS:
        return None
    async with tenant_txn(ident.tenant_id) as s:
        found = await FT.delivered_file(s, account_id, sheet)
    return await _read_book(request, ident, found, sheet, builder_only=builder_only)


async def _read_book(
    request: Request,
    ident: Identity,
    found: FT.Delivered | None,
    sheet: str,
    *,
    builder_only: bool = False,
) -> tuple[FT.Book, FT.Delivered] | None:
    """The workbook ``found`` read back (``file_tabs.book_of``); see ``_book``."""
    if found is None or (builder_only and found.rows is None):
        # a file without the builder's row count is never the files' table (builder_main)
        return None
    payloads = request.app.state.container.payloads
    book = await FT.book_of(payloads, ident.tenant_id, found, FT.main_sheet_of(sheet))
    return (book, found) if book is not None else None


def _file_main(
    run: UUID | None, delivered: tuple[FT.Book, FT.Delivered] | None, sheet: str
) -> FT.Tab | None:
    """The main tab read from the file when the delivered run's stored table cannot be shown
    (``sheets._file_table`` passed it over, for example a table stored under an earlier column
    set): the screen then shows the builder's file's own table rather than the live research
    table with other columns (``file_tabs.builder_main``)."""
    if run is not None or delivered is None:
        return None
    return FT.builder_main(delivered[0], delivered[1], sheet)


async def _export_main(
    request: Request, ident: Identity, account_id: UUID, run: UUID | None, sheet: str
) -> FT.Tab | None:
    """The file's own table an export writes (``_file_main``); the workbook is not read when
    the stored table is in use."""
    if run is not None:
        return None
    book = await _book(request, ident, account_id, sheet, builder_only=True)
    return _file_main(None, book, sheet)


def _grid_query(
    main: FT.Tab, sheet: str, sort: str | None, dir: str | None, offset: int, limit: int
) -> SH.Query:
    try:
        return FT.grid_query(main, SH.SHEETS[sheet], sort, dir, offset, limit)  # type: ignore[no-any-return]
    except SH.BadSheetQuery:
        raise APIProblem(
            422,
            "bad_sheet_query",
            "Unknown filter or sort",
            "Choose a filter and a column from the table.",
        ) from None


@router.get("/{domain}/workspace", response_model=C.AccountWorkspace)
async def account_workspace(
    domain: str, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """The Account workspace screen for one account. Once files are delivered, the counts of
    the tables the files print are the files' row counts (the sheets show those rows).

    One transaction reads everything. The delivered run's tables are materialised first only
    when they are not yet (once per run), and the delivered workbooks are opened, all at once,
    only for a count that is not already known (``file_tabs.main_rows``)."""
    started = time.perf_counter()
    printed = (("people", "people"), ("jobs", "jobs"), ("technology", "tech"))
    materialised = False
    for _ in range(2):  # the second pass only after the run's tables were just built
        async with tenant_txn(ident.tenant_id) as s:
            account_id = await account_id_of(s, domain)
            run = await SH.delivered_run(s, account_id)
            if run is not None and not materialised and not await SH.is_materialised(s, run):
                missing = True
            else:
                missing = False
                out = await W.workspace(s, account_id, ident.user_id, datetime.now(UTC))
                delivered = await SH.file_counts(s, account_id, run)
                # the files of the sheets whose stored table cannot be shown
                files = await FT.delivered_files(
                    s, account_id, [sheet for sheet, _ in printed if sheet not in delivered], run
                )
        if not missing:
            break
        await _delivered_tables(request, ident, account_id)
        materialised = True
    queried = time.perf_counter()
    counts = dict(out["counts"])
    # coverage keeps its "done of all items" counts (the tracker's own)
    payloads = request.app.state.container.payloads
    asked = [sheet for sheet, _ in printed if sheet in files]
    rows_of = await asyncio.gather(
        *(FT.main_rows(payloads, ident.tenant_id, files[sheet], sheet) for sheet in asked)
    )
    opened = dict(zip(asked, rows_of, strict=True))
    for sheet, key in printed:
        if sheet in delivered:
            counts[key] = delivered[sheet]
        elif opened.get(sheet) is not None:
            # the sheet shows the delivered file's own table (``_file_main``): its row count
            counts[key] = opened[sheet]
    counts["facts"] = delivered.get("facts", 0)
    result: dict[str, Any] = _client_value({**out, "counts": counts, "latest_run": None})
    log.info(
        "workspace.timing",
        extra={
            "queries_ms": round(1000 * (queried - started)),
            "files_ms": round(1000 * (time.perf_counter() - queried)),
            "files_opened": len(opened),
        },
    )
    return result


@router.get("/{domain}/file-analysis", response_model=C.FileAnalysisRun)
async def file_analysis(
    domain: str,
    run: UUID | None = Query(default=None),
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """The account's file analysis (the given run, else the latest file-mode run): its state,
    when it ran, how many files it read and whether public sources were added. 404 when the
    account has none."""
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        row = (
            await s.execute(
                text(
                    "SELECT r.id, r.status, r.created_at, r.finished_at,"
                    " (SELECT count(*) FROM run_documents d WHERE d.run_id = r.id) AS files,"
                    " (SELECT c.id FROM account_runs c"
                    "   WHERE c.run_input->>'linked_file_run' = r.id::text LIMIT 1) AS public_run,"
                    " EXISTS (SELECT 1 FROM coverage_rows v WHERE v.run_id = r.id"
                    "   AND v.reason_code = 'source_unavailable') AS model_unread"
                    " FROM account_runs r WHERE r.account_id = :a AND r.input_mode = 'file'"
                    " AND (CAST(:run AS uuid) IS NULL OR r.id = CAST(:run AS uuid))"
                    " ORDER BY r.created_at DESC LIMIT 1"
                ),
                {"a": account_id, "run": run},
            )
        ).first()
        name = await _account_name(s, account_id)
    if row is None:
        raise HTTPException(404, "not found")
    return {
        "run_id": str(row.id),
        "account_name": name,
        "status": row.status,
        "done": row.status in ("COMPLETED", "PARTIAL", "FAILED", "CANCELED", "BUDGET_EXCEEDED"),
        "created_at": row.created_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "files": int(row.files or 0),
        "public_run_id": str(row.public_run) if row.public_run else None,
        "model_unread": bool(row.model_unread),
    }


@router.get("/{domain}/deliverables", response_model=C.DeliverableFiles)
async def account_deliverables(domain: str, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """The account's current deliverables (newest version of each)."""
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        return {"files": await W.deliverables(s, account_id)}


@router.get("/{domain}/deliverables/download")
async def download_deliverables(
    domain: str,
    request: Request,
    ids: str = Query(
        min_length=1, max_length=400, pattern=r"^(all|[0-9a-f-]{36}(,[0-9a-f-]{36}){0,9})$"
    ),
    ident: Identity = Depends(authorize),
) -> Response:
    """One file as itself, or several as one zip named after the account."""
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        current = {f["id"]: f for f in await W.deliverables(s, account_id)}
        wanted = list(current) if ids == "all" else ids.split(",")
        if not wanted or any(i not in current for i in wanted):
            raise HTTPException(404, "not found")
        rows = (
            await s.execute(
                text(
                    "SELECT id, filename, object_key, sha256 FROM deliverables WHERE id = ANY(:i)"
                ),
                {"i": [UUID(i) for i in wanted]},
            )
        ).all()
        for r in rows:
            try:
                require_owned(ident.tenant_id, r.object_key)
            except PathRefused:
                raise HTTPException(404, "not found") from None
        await audit_action(
            s,
            request,
            audit_class="export",
            action="download_deliverables",
            resource_type="account",
            resource_id=account_id,
        )
    store = request.app.state.container.payloads
    if len(rows) == 1:
        r = rows[0]
        body = await store.get(r.object_key)
        ext = r.filename.rsplit(".", 1)[-1]
        return Response(
            body,
            media_type=MEDIA.get(ext, "application/octet-stream"),
            headers={"Content-Disposition": f'attachment; filename="{r.filename}"'},
        )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for r in sorted(rows, key=lambda r: r.filename):
            archive.writestr(r.filename, await store.get(r.object_key))
    name = f"{domain.split('.')[0]}-files.zip"
    return Response(
        buffer.getvalue(),
        media_type=MEDIA["zip"],
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _sheet_query(
    sheet: str,
    filter: str | None,
    sort: str | None,
    dir: str | None,
    offset: int,
    limit: int,
    run_id: UUID | None = None,
) -> SH.Query:
    try:
        return SH.parse(sheet, filter, sort, dir, offset, limit, run_id=run_id)
    except SH.SheetNotFound:
        raise HTTPException(404, "not found") from None
    except SH.BadSheetQuery:
        raise APIProblem(
            422,
            "bad_sheet_query",
            "Unknown filter or sort",
            "Choose a filter and a column from the table.",
        ) from None


def _known(sheet: str) -> None:
    """404 for a sheet that does not exist (before any work for it)."""
    if sheet not in SH.SHEETS and sheet not in SH.FILE_TABLES:
        raise HTTPException(404, "not found")


async def _account_name(s: Any, account_id: UUID) -> str:
    return str(
        await s.scalar(
            text("SELECT coalesce(name, domain) FROM accounts WHERE id = :a"), {"a": account_id}
        )
    )


@router.get("/{domain}/sheets/{sheet}", response_model=C.Sheet)
async def account_sheet(
    domain: str,
    sheet: str,
    request: Request,
    filter: str | None = Query(default=None, max_length=40),
    sort: str | None = Query(default=None, max_length=40),
    dir: str | None = Query(default=None, max_length=4),
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=200, ge=1, le=SH.MAX_PAGE),
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """One page of a deep-dive table, filtered and sorted on the server. Once files are
    delivered, a table the files print shows the files' own table (``sheets.file_sheet``)."""
    _known(sheet)
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        name = await _account_name(s, account_id)
    run = await _file_run(request, ident, account_id, sheet)
    # with a stored table the file gives the tabs; without one, only a builder's file is used
    delivered = await _book(request, ident, account_id, sheet, builder_only=run is None)
    main = _file_main(run, delivered, sheet)
    if main is not None:
        q = _grid_query(main, sheet, sort, dir, offset, limit)
        out = FT.grid_page(main, q)
    else:
        q = _sheet_query(sheet, filter, sort, dir, offset, limit, run)
        async with tenant_txn(ident.tenant_id) as s:
            out = await SH.read(s, q, account_id, ident.user_id, datetime.now(UTC))
    response = {**out, "account_name": name}
    if delivered is not None and (main is not None or q.sheet.table is not None):
        # the delivered workbook's sheets, as tabs, and the file itself
        response["tabs"] = FT.tab_refs(delivered[0])
        response["file_id"] = delivered[1].id
    return response if q.sheet.private else _client_value(response)


@router.get("/{domain}/sheets/{sheet}/tabs/{tab}", response_model=C.SheetTab)
async def account_sheet_tab(
    domain: str,
    sheet: str,
    tab: str,
    request: Request,
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """One sheet of the delivered workbook (Summary, Platform Ranking, Platform teams, Counts),
    as the file prints it: its text lines and tables, formulas computed, links kept."""
    _known(sheet)
    if sheet not in FT.KINDS or not re.fullmatch(r"[a-z0-9-]{1,48}", tab):
        raise HTTPException(404, "not found")
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        name = await _account_name(s, account_id)
    delivered = await _book(request, ident, account_id, sheet)
    found = delivered[0].tab(tab) if delivered is not None else None
    if delivered is None or found is None:
        raise HTTPException(404, "not found")
    book, file = delivered
    result: dict[str, Any] = _client_value(
        {
            "key": sheet,
            "title": SH.SHEETS[sheet].title,
            "file": file.filename,
            "account_name": name,
            "tab": found.key,
            "tab_label": found.name,
            "tabs": FT.tab_refs(book),
            "file_id": file.id,
            "blocks": FT.blocks_out(found),
        }
    )
    return result


def _client_value(value: Any) -> Any:
    """Scrub tool names and refuse remaining tool/person/domain hits before a JSON response."""
    try:
        return sanitize_client_value(value)
    except GuardViolation as error:
        raise APIProblem(
            422,
            "client_output_blocked",
            "Response held back",
            "This response failed a safety check and could not be returned.",
        ) from error


@router.get("/{domain}/sheets/{sheet}/xlsx")
async def account_sheet_xlsx(
    domain: str,
    sheet: str,
    request: Request,
    filter: str | None = Query(default=None, max_length=40),
    sort: str | None = Query(default=None, max_length=40),
    dir: str | None = Query(default=None, max_length=4),
    ident: Identity = Depends(authorize),
) -> Response:
    """Every row of the filtered, sorted table as an Excel workbook (an audited export; a table
    longer than one Excel sheet continues on the next; ``replica.table_export``)."""
    import os
    import tempfile
    from pathlib import Path

    from fastapi.responses import FileResponse
    from starlette.background import BackgroundTask

    from account_intelligence.replica import table_export as TX

    _known(sheet)
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
    run = await _file_run(request, ident, account_id, sheet)
    main = await _export_main(request, ident, account_id, run, sheet)
    q = (
        _grid_query(main, sheet, sort, dir, 0, SH.MAX_PAGE)
        if main is not None
        else _sheet_query(sheet, filter, sort, dir, 0, SH.MAX_PAGE, run)
    )
    fd, tmp = tempfile.mkstemp(prefix="sheet-", suffix=".xlsx")
    os.close(fd)
    try:
        async with tenant_txn(ident.tenant_id) as s:
            name = await _account_name(s, account_id)
            source = (
                FT.iter_rows(main, q)
                if main is not None
                else TX.rows(s, q, account_id, ident.user_id, datetime.now(UTC))
            )
            try:
                await TX.write_xlsx(Path(tmp), q, name, source)
            except SH.ExportBlocked as err:
                log.warning("sheet_export_blocked", extra={"checks": ",".join(err.checks)})
                raise APIProblem(
                    422,
                    "export_blocked",
                    "Export held back",
                    "This table could not be exported because it failed a safety check."
                    " Try again later.",
                ) from err
            await audit_action(
                s,
                request,
                audit_class="export",
                action="export_sheet",
                resource_type="account",
                resource_id=account_id,
            )
    except BaseException:
        # remove the temporary file on any exit, cancellation included, then re-raise
        Path(tmp).unlink(missing_ok=True)
        raise
    return FileResponse(
        tmp,
        media_type=MEDIA["xlsx"],
        filename=f"{_stem(name, q)}.xlsx",
        background=BackgroundTask(lambda: Path(tmp).unlink(missing_ok=True)),
    )


@router.get("/{domain}/sheets/{sheet}/csv")
async def account_sheet_csv(
    domain: str,
    sheet: str,
    request: Request,
    filter: str | None = Query(default=None, max_length=40),
    sort: str | None = Query(default=None, max_length=40),
    dir: str | None = Query(default=None, max_length=4),
    ident: Identity = Depends(authorize),
) -> Response:
    """Every row of the filtered, sorted table as CSV, streamed (an audited export)."""
    from fastapi.responses import StreamingResponse

    from account_intelligence.replica import table_export as TX

    _known(sheet)
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
    run = await _file_run(request, ident, account_id, sheet)
    main = await _export_main(request, ident, account_id, run, sheet)
    q = (
        _grid_query(main, sheet, sort, dir, 0, SH.MAX_PAGE)
        if main is not None
        else _sheet_query(sheet, filter, sort, dir, 0, SH.MAX_PAGE, run)
    )
    async with tenant_txn(ident.tenant_id) as s:
        name = await _account_name(s, account_id)
        await audit_action(
            s,
            request,
            audit_class="export",
            action="export_sheet_csv",
            resource_type="account",
            resource_id=account_id,
        )

    async def body() -> Any:
        """The rows, read page by page in their own transaction while they stream."""
        if main is not None:
            async for chunk in TX.csv_chunks(q, FT.iter_rows(main, q)):
                yield chunk
            return
        async with tenant_txn(ident.tenant_id) as s:
            rows = TX.rows(s, q, account_id, ident.user_id, datetime.now(UTC))
            async for chunk in TX.csv_chunks(q, rows):
                yield chunk

    return StreamingResponse(
        body(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_stem(name, q)}.csv"'},
    )


def _stem(account_name: str, q: SH.Query) -> str:
    """A file name: the account and the table (letters, digits and hyphens only)."""
    return re.sub(r"[^A-Za-z0-9]+", "-", f"{account_name} {q.sheet.file.rsplit('.', 1)[0]}").strip(
        "-"
    )
