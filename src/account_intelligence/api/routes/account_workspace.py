"""The Account workspace screen and its deliverable files.

* ``GET /v1/accounts/{domain}/workspace``: narrative, key findings, what changed (after a refresh
  only), deep-dive counts and files.
* ``GET /v1/accounts/{domain}/deliverables``: the newest version of each deliverable.
* ``GET /v1/accounts/{domain}/deliverables/download?ids=a,b``: one id downloads that file as
  itself; several (or ``all``) download one zip. Every download is an audited export. A file of
  another account or tenant is simply not found.
* ``GET /v1/accounts/{domain}/sheets/{sheet}``: one page of a deep-dive table; once a workbook
  is delivered, the table of that file (stored rows, else the file's own main table), with the
  file's sheets as tabs (``.../tabs/{tab}``) and long cells cut (``.../cell`` for one whole cell).
  The client-output guard withholds a cell, never the page.
"""

from __future__ import annotations

import asyncio
import io
import re
import time
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy import text

from account_intelligence import archive as LA
from account_intelligence import archive_records as AR
from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.ratelimit import limit_user
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import file_tabs as FT
from account_intelligence.replica import sheets as SH
from account_intelligence.replica import workspace as W
from agents.errors import GuardViolation
from agents.naming import sanitize_client_value, third_party_domain_hits
from exports.policy import restricted_terms
from exports.readers.exclusions import text_excluded
from obs.logging import logger
from tenancy.storage import PathRefused, require_owned

log = logger(__name__)
router = APIRouter(prefix="/accounts", tags=["accounts"])
register(
    "GET", "/v1/accounts/{domain}/workspace", tenant_route("read_public", "read", step_up=False)
)
register("GET", "/v1/accounts/{domain}/archive", tenant_route("read_public", "read", step_up=False))
register(
    "GET", "/v1/accounts/{domain}/archive/list", tenant_route("read_public", "read", step_up=False)
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
    "/v1/accounts/{domain}/sheets/{sheet}/cell",
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
    found = await session.scalar(
        text("SELECT id FROM accounts WHERE domain = :d AND deleted_at IS NULL"), {"d": domain}
    )
    if found is None:
        raise HTTPException(404, "not found")
    return UUID(str(found))


# the runs whose files this process is building again right now (a second request does not start
# a second build; it sees the held-back files until the first finishes), and the builds a rule
# refused, by exclusion set (not tried again for REBUILD_RETRY_S: a refused build is the same
# build every time, and each try costs a full render)
_REBUILDING: set[tuple[UUID, UUID]] = set()
_REFUSED: dict[tuple[UUID, UUID, str], float] = {}
REBUILD_RETRY_S = 600.0


async def refresh_stale_files(request: Request, ident: Identity, account_id: UUID) -> None:
    """Build the account's files again when they were built under another exclusion list than the
    tenant's current one (an exclusion added after the research, leakage review 2026-10-30, F1).
    Until the new set is stored the stale files are neither listed nor served
    (``workspace.deliverables``), so a failed build leaves them held back, never shown. The build
    reads no provider and costs no credit."""
    async with tenant_txn(ident.tenant_id) as s:
        if not await W.stale_kinds(s, account_id):
            return
        run = await SH.delivered_run(s, account_id)
    builder = getattr(request.app.state.container, "builder", None)
    if run is None or builder is None or (ident.tenant_id, run) in _REBUILDING:
        return
    _REBUILDING.add((ident.tenant_id, run))
    try:
        result = await builder.build_all(str(ident.tenant_id), str(run))
        if result.status != "complete":
            log.warning(
                "files_rebuild_refused", extra={"run_id": str(run), "reason": result.reason}
            )
    except Exception as error:  # noqa: BLE001 - the stale files stay held back
        log.warning("files_rebuild_failed", extra={"error": type(error).__name__})
    finally:
        _REBUILDING.discard((ident.tenant_id, run))


async def _delivered_tables(request: Request, ident: Identity, account_id: UUID) -> None:
    """Materialise the client tables of the account's delivered run, once per run and exclusion
    list (``views.materialize``), so the deep-dive sheets can show the files' own tables; files
    built under an older exclusion list are built again first (``refresh_stale_files``). A run
    that cannot be materialised leaves the live sheets in place; the failure is logged, never
    shown."""
    from account_intelligence.api.routes.accounts import materialized

    await refresh_stale_files(request, ident, account_id)
    async with tenant_txn(ident.tenant_id) as s:
        run = await SH.delivered_run(s, account_id)
    if run is None:
        return
    try:
        await materialized(request, ident.tenant_id, run)
    except Exception as error:  # noqa: BLE001 - the live sheets stand in for the file tables
        log.warning("sheet_tables_unavailable", extra={"error": type(error).__name__})


async def prepare_delivered(request: Request, ident: Identity, account_id: UUID) -> None:
    """Public form of ``_delivered_tables`` for the other routes that read the delivered files
    and tables (Download everything): files and tables made under an older exclusion list are
    built again first."""
    await _delivered_tables(request, ident, account_id)


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
    set): the screen then shows the delivered file's own table, by the file's own header
    (``file_tabs.builder_main``, else ``main_table``), never the live research table with other
    columns (owner 2026-10-29: a delivered account never falls back to the live sheet)."""
    if run is not None or delivered is None:
        return None
    return FT.builder_main(delivered[0], delivered[1], sheet) or FT.main_table(delivered[0])


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
    main: FT.Tab,
    sheet: str,
    sort: str | None,
    dir: str | None,
    offset: int,
    limit: int,
    filter: str | None = None,
) -> SH.Query:
    try:
        return FT.grid_query(main, SH.SHEETS[sheet], sort, dir, offset, limit, filter)  # type: ignore[no-any-return]
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
            if (
                run is not None
                and not materialised
                and (not await SH.is_materialised(s, run) or await W.stale_kinds(s, account_id))
            ):
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
async def account_deliverables(
    domain: str, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """The account's current deliverables (newest version of each)."""
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
    await refresh_stale_files(request, ident, account_id)
    async with tenant_txn(ident.tenant_id) as s:
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
    await refresh_stale_files(request, ident, account_id)
    async with tenant_txn(ident.tenant_id) as s:
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
    # the delivered workbook gives the tabs, and its own table when the stored one cannot stand
    delivered = await _book(request, ident, account_id, sheet, builder_only=run is None)
    main = _file_main(run, delivered, sheet)
    if main is not None:
        q = _grid_query(main, sheet, sort, dir, offset, limit, filter)
        out = FT.grid_page(main, q)
    else:
        q = _sheet_query(sheet, filter, sort, dir, offset, limit, run)
        async with tenant_txn(ident.tenant_id) as s:
            out = await SH.read(s, q, account_id, ident.user_id, datetime.now(UTC))
    async with tenant_txn(ident.tenant_id) as s:
        # the archived copy behind each link of the page (link archive): a dead link still
        # opens what the research captured
        await SH.attach_archives(s, account_id, out["rows"], domain)
    response = {**out, "account_name": name}
    if delivered is not None and (main is not None or q.sheet.table is not None):
        # the delivered workbook's sheets, as tabs, and the file itself
        response["tabs"] = FT.tab_refs(delivered[0])
        response["file_id"] = delivered[1].id
    if q.sheet.private:
        SH.cap_rows(response["rows"])
        return response
    return _client_page(response, sheet, await _forbidden_terms(ident, account_id))


async def _forbidden_terms(ident: Identity, account_id: UUID) -> tuple[str, ...]:
    """The terms no cell of a table may carry: other accounts' names and domains and the
    excluded companies (the run's ``forbidden_terms`` of the exports). A tenant whose settings no
    longer validate keeps the other-account terms only: the tables were built and guarded by the
    exports already; the archive routes fail closed instead (``_archive_policy``)."""
    try:
        return (await _archive_policy(ident, account_id)).forbidden
    except APIProblem:
        async with tenant_txn(ident.tenant_id, system=True) as s:
            others = await LA.other_account_terms(s, account_id)
        return restricted_terms(others, AR.SOURCE)


def _client_page(
    response: dict[str, Any], sheet: str, extra_terms: Sequence[str] = ()
) -> dict[str, Any]:
    """A page of a table made safe for the client: long cells cut (``sheets.cap_rows``), each
    cell scrubbed and, when a guarded term remains, withheld on its own (``sheets.scrub_rows``,
    as the exports do), then the rest of the response through the guard. The page always
    loads; the owner's reference Jobs sheet was held back whole for the word "parallel" in a
    description (audit 2026-10-29, F1)."""
    rows = response["rows"]
    SH.cap_rows(rows)
    held = SH.scrub_rows(rows, extra_terms)
    if held:
        log.info("sheet_cells_withheld", extra={"sheet": sheet, "cells": held})
    rest = _client_value({k: v for k, v in response.items() if k != "rows"})
    return {**rest, "rows": rows}


@router.get("/{domain}/sheets/{sheet}/cell", response_model=C.SheetCellText)
async def account_sheet_cell(
    domain: str,
    sheet: str,
    request: Request,
    n: int = Query(ge=1, le=1_000_000),
    col: str = Query(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$"),
    filter: str | None = Query(default=None, max_length=40),
    sort: str | None = Query(default=None, max_length=40),
    dir: str | None = Query(default=None, max_length=4),
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """One whole cell of a deep-dive table (the page shows a long cell cut to ``CELL_CAP``):
    row ``n`` under the same filter and sort as the page, column ``col``. 404 past the table
    or for a column the table does not have; a guarded cell reads as withheld."""
    _known(sheet)
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
    run = await _file_run(request, ident, account_id, sheet)
    delivered = await _book(request, ident, account_id, sheet, builder_only=run is None)
    main = _file_main(run, delivered, sheet)
    found: dict[str, Any] | None
    if main is not None:
        q = _grid_query(main, sheet, sort, dir, n - 1, 1, filter)
        found = FT.grid_cell(main, q, n, col)
    else:
        q = _sheet_query(sheet, filter, sort, dir, n - 1, 1, run)
        if col not in {c.key for c in q.sheet.cols}:
            raise HTTPException(404, "not found")
        async with tenant_txn(ident.tenant_id) as s:
            page = await SH.read(s, q, account_id, ident.user_id, datetime.now(UTC))
        row = next((r for r in page["rows"] if r["n"] == n), None)
        found = (
            {"n": n, "col": col, "text": row["cells"].get(col, ""), "href": row["links"].get(col)}
            if row is not None
            else None
        )
    if found is None:
        raise HTTPException(404, "not found")
    if q.sheet.private:
        return {**found, "withheld": False}
    rows: list[dict[str, Any]] = [
        {"n": n, "cells": {col: found["text"]}, "links": {}, "link": None}
    ]
    held = SH.scrub_rows(rows, await _forbidden_terms(ident, account_id))
    href = found.get("href")
    if isinstance(href, str) and third_party_domain_hits(href):
        href = None  # a provider address is dropped, as the page route drops it (never a 422)
    result: dict[str, Any] = _client_value(
        {
            "n": n,
            "col": col,
            "text": rows[0]["cells"][col],
            "href": href,
            "withheld": bool(held),
        }
    )
    return result


@dataclass(frozen=True)
class _ArchivePolicy:
    """What the archive routes hide, from the tenant's settings and the other accounts: the
    exclusion filter (people and companies, ``tenancy.settings``) and the terms no archived text
    may carry (other accounts' names and domains, excluded companies), as the exports apply them
    (``exports.readers.exclusions``, ``exports.readers.pg``)."""

    rule: Any
    forbidden: tuple[str, ...]

    def hides(self, *texts: str | None, url: str | None = None) -> bool:
        """True when the copy or row must not be served at all: it names an excluded person,
        or its address is an excluded company's or a data provider's."""
        if url and third_party_domain_hits(url):
            return True
        return bool(text_excluded(self.rule, *texts, url=url))


async def _archive_policy(ident: Identity, account_id: UUID) -> _ArchivePolicy:
    """The exclusion rule and the forbidden terms for the archive of one account (409 when the
    tenant's settings no longer validate, as every other reader of them)."""
    from account_intelligence.api.routes.accounts import exclusion_rule

    async with tenant_txn(ident.tenant_id) as s:
        rule = await exclusion_rule(s)
    # the names and domains of every OTHER account of the client, including colleagues' private
    # workspaces: used only to refuse text that names them, never returned
    async with tenant_txn(ident.tenant_id, system=True) as s:
        terms = await LA.other_account_terms(s, account_id)
    terms += sorted(rule.companies)
    return _ArchivePolicy(rule, restricted_terms(terms, AR.SOURCE))


def _withhold_leaks(
    value: Any, held: list[str], path: str = "", forbidden: Sequence[str] = ()
) -> Any:
    """The value with each text the client-output guard refuses blanked (and its path listed in
    ``held``), tool names scrubbed: one refused field never costs the whole archived copy.
    ``forbidden``: terms no text may carry (other accounts, excluded companies)."""
    from agents.naming import client_hits, scrub_client_text

    if isinstance(value, str):
        shown, refused = scrub_client_text(value)
        if not refused and forbidden and client_hits(shown, forbidden):
            refused = True
        if refused:
            held.append(path)
            return ""
        return shown
    if isinstance(value, dict):
        return {
            k: _withhold_leaks(v, held, f"{path}.{k}" if path else str(k), forbidden)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_withhold_leaks(v, held, path, forbidden) for v in value]
    return value


def _record_texts(record: dict[str, Any] | None) -> list[str]:
    """Every string a structured record holds, for the exclusion check."""
    if not record:
        return []
    out: list[str] = []
    for v in record.values():
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            out += [x for x in v if isinstance(x, str)]
            out += [t for x in v if isinstance(x, dict) for t in x.values() if isinstance(t, str)]
    return out


@router.get("/{domain}/archive", response_model=C.ArchivedCopy)
async def account_archive(
    domain: str,
    request: Request,
    url: str = Query(min_length=8, max_length=2048),
    download: bool = Query(False),
    ident: Identity = Depends(authorize),
) -> Any:
    """The archived copy of one address the research stored for this account (the newest
    capture): the original address, the kind of link, when it was captured, the text and, for a
    posting, its structured record (``posting-archive/1``). JSON only: the page a person reads is
    the website's (``/accounts/{domain}/archive?url=``), which fetches this. ``download`` answers
    the same as a file, whole and audited as an export; the JSON view cuts the text to
    ``archive.MAX_COPY_TEXT`` (``truncated`` says so). A free-text field the output guard refuses
    is blanked and named under ``withheld``. 404 when the address was never stored for the
    account (another tenant's looks the same), when it is a profile copy (internal, never served),
    or when the copy names a person the client excluded or lives on an excluded or provider
    domain."""
    from fastapi.responses import JSONResponse

    await limit_user(ident, "archive_read")
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        # profile copies are internal inputs of the people matcher: never listed, never served
        found = await LA.lookup(s, account_id, [url], kinds=LA.CLIENT_KINDS)
    link = found.get(url)
    if link is None:
        raise HTTPException(404, "not found")
    policy = await _archive_policy(ident, account_id)
    if policy.hides(link.title, url=link.url):
        raise HTTPException(404, "not found")
    try:
        require_owned(ident.tenant_id, link.payload_ref)
        copy = await LA.archived_copy(
            request.app.state.container.payloads,
            link,
            max_text=None if download else LA.MAX_COPY_TEXT,
        )
    except (PathRefused, ValueError, LookupError):
        raise HTTPException(404, "not found") from None
    except Exception:  # noqa: BLE001 - a missing or unreadable body reads as not found
        raise HTTPException(404, "not found") from None
    if policy.hides(copy.title, copy.text, *_record_texts(copy.record), url=copy.url):
        raise HTTPException(404, "not found")
    held: list[str] = []
    body = _withhold_leaks(
        {
            "url": copy.url,
            "kind": copy.kind,
            "archived_at": copy.archived_at.isoformat(),
            "title": copy.title,
            "text": copy.text,
            "record": copy.record,
            "truncated": copy.truncated,
        },
        held,
        forbidden=policy.forbidden,
    )
    body["withheld"] = sorted(set(held))
    if isinstance(body.get("record"), dict) and body["record"].get("content_hash"):
        # a cleaned page or news copy names its account and its own id in the file it gives
        digest = str(body["record"]["content_hash"]).split(":")[-1][:12]
        body["record"]["account_domain"] = domain
        body["record"]["record_id"] = f"arc_{domain}_{digest}"
    if held and isinstance(body.get("record"), dict):
        body["record"]["withheld"] = sorted({*body["record"].get("withheld", []), *held})
    if download:
        async with tenant_txn(ident.tenant_id) as s:
            # a file leaves the product: audited as an export, like every other download
            await audit_action(
                s,
                request,
                audit_class="export",
                action="download_archive",
                resource_type="account",
                resource_id=account_id,
            )
        name = (
            str(copy.record.get("schema", "archive")).split("/")[0]
            if copy.record is not None
            else "archived-copy"
        )
        payload = body["record"] if copy.record is not None else body
        return JSONResponse(
            payload,
            headers={
                "Content-Disposition": f'attachment; filename="{name}.json"',
                "Cache-Control": "no-store",
            },
        )
    return body


@router.get("/{domain}/archive/list", response_model=C.ArchiveList)
async def account_archive_list(
    domain: str,
    request: Request,
    kind: str | None = Query(None, max_length=16),
    offset: int = Query(0, ge=0, le=100_000),
    limit: int = Query(50, ge=1, le=200),
    title: str | None = Query(None, max_length=100),
    publisher: str | None = Query(None, max_length=100),
    published: str | None = Query(None, max_length=100),
    source_type: str | None = Query(None, max_length=100),
    grade: str | None = Query(None, max_length=100),
    event_type: str | None = Query(None, max_length=100),
    person: str | None = Query(None, max_length=100),
    lane: str | None = Query(None, max_length=100),
    collected: str | None = Query(None, max_length=100),
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """The account's archive index: the newest copy of each stored address, newest capture first,
    one page (``limit`` at most 200). ``kind`` narrows it (``posting``, ``page``, ``feed_item``,
    ``detection``); ``profile`` copies are internal and never listed. A posting also gives its
    status when captured, location and requisition (stored beside the row; no body is opened for
    them). A row that names a person the client excluded, or sits on an excluded or provider
    domain, is left out. Empty for an account of another tenant. Each column of the index
    filters (``title``, ``publisher``, ``published``, ``source_type``, ``grade``, ``event_type``,
    ``person``, ``lane``, ``collected``): a case-insensitive "contains" on the field stored beside
    the row."""

    await limit_user(ident, "archive_read")
    column_filters = {
        k: v
        for k, v in {
            "title": title,
            "publisher": publisher,
            "published": published,
            "source_type": source_type,
            "grade": grade,
            "event_type": event_type,
            "person": person,
            "lane": lane,
            "collected": collected,
        }.items()
        if v
    }
    async with tenant_txn(ident.tenant_id) as s:
        account_id = await account_id_of(s, domain)
        links, total = await LA.list_latest(
            s,
            account_id,
            kind=kind,
            offset=offset,
            limit=limit,
            kinds=LA.CLIENT_KINDS,
            filters=column_filters,
        )
    policy = await _archive_policy(ident, account_id)
    items = await LA.list_items(
        request.app.state.container.payloads,
        links,
        readable=lambda ref: _owned(ident.tenant_id, ref),
    )
    shown = [i for i in items if not policy.hides(i.title, i.location, i.requisition, url=i.url)]
    held: list[str] = []
    rows = _withhold_leaks(
        [
            {
                "url": i.url,
                "title": i.title,
                "kind": i.kind,
                "archived_at": i.archived_at.isoformat(),
                "status_at_capture": i.status_at_capture,
                "location": i.location,
                "requisition": i.requisition,
                "record_schema": i.record_schema,
                "publisher": i.publisher,
                "published_date": i.published_date,
                "source_type": i.source_type,
                "fact_grade": i.fact_grade,
                "event_types": i.event_types,
                "people": i.people,
                "lanes": i.lanes,
            }
            for i in shown
        ],
        held,
        forbidden=policy.forbidden,
    )
    nxt = offset + len(links)
    return {
        "items": rows,
        "total": max(total - (len(items) - len(shown)), 0),
        "offset": offset,
        "limit": limit,
        "next_offset": nxt if nxt < total else None,
    }


def _owned(tenant_id: UUID, ref: str) -> bool:
    """True when the payload key belongs to the tenant (``tenancy.storage.require_owned``)."""
    try:
        require_owned(tenant_id, ref)
    except PathRefused:
        return False
    return True


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
    # each cell scrubbed and, when a guarded term remains, withheld on its own: the tab loads
    blocks = FT.blocks_out(found)
    held = FT.scrub_blocks(blocks, await _forbidden_terms(ident, account_id))
    if held:
        log.info("sheet_cells_withheld", extra={"sheet": sheet, "tab": found.key, "cells": held})
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
        }
    )
    return {**result, "blocks": blocks}


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
        _grid_query(main, sheet, sort, dir, 0, SH.MAX_PAGE, filter)
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
        _grid_query(main, sheet, sort, dir, 0, SH.MAX_PAGE, filter)
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
