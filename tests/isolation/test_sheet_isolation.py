"""Two clients, the same workspace zip, every deep-dive sheet and the account-page counts (PR1 of
the upload hardening, 2026-11-04). SYNTHETIC data only.

Tenants A and B each get an account on the same domain and upload the same workspace zip through
the production path (scan, parts, index, import). The few sheets an upload cannot fill (web
searches, calls, the five file-shaped sheets) get matching canary rows in both
tenants, so every sheet has rows on both sides. Then, for every sheet:

* with row level security on (A's transaction as the client's strongest user, the system scope),
  A's rows carry no row id of B, A sees as many rows as B, and asking for B's account (or B's
  delivered run) returns nothing;
* with row level security OFF (a superuser, which RLS never applies to) and A's tenant bound to
  the session, asking for B's account or run still returns nothing: the ``tenant_id = :t``
  predicates the sheets carry hold on their own (``sheets.params``);
* the account-page counts follow the same two rules.

An ``Internal/`` member of the zip (owner-only within the client) is listed only to the uploader
and never becomes a passage, a table, a fact or an imported row.

GUARANTEE(test_every_sheet_and_count_is_isolated_between_two_clients_with_the_same_workspace)
GUARANTEE(test_an_internal_member_is_listed_only_to_its_uploader_and_never_read)
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg.types.json import Jsonb
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from account_intelligence.db.session import bind_tenant, session_tenant, tenant_txn
from account_intelligence.replica import sheets as SH
from account_intelligence.replica import table_export as TX
from account_intelligence.replica.workspace import account_rows, workspace
from tests.conftest import ADMIN_DSN, _make_account
from tests.documents import workspace_zip
from tests.documents.test_workspace_zip_import_db import _zip_with_twins
from tests.documents.world import service, upload
from tests.isolation.matrix.plant import plant, tenant_tables

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

UPLOADER = "user-1"  # tests.documents.world.upload
OWNER = "test-owner"  # the account's owner (tests.conftest._make_account)
INTERNAL = f"{workspace_zip.FOLDER}/Internal/pricing_position.md"
INTERNAL_TEXT = "INTERNAL-ONLY-CANARY our walk-away price is confidential"
ALL_SHEETS = sorted({*(("live", k) for k in SH.SHEETS), *(("file", k) for k in SH.FILE_TABLES)})


def _zip() -> bytes:
    """The workspace zip of the import tests plus an ``Internal/`` member."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(_zip_with_twins())) as base,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z,
    ):
        for info in base.infolist():
            z.writestr(info, base.read(info))
        z.writestr(INTERNAL, f"# Pricing position\n\n{INTERNAL_TEXT}\n")
    return out.getvalue()


def _admin(sql: str, *args: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        return c.execute(sql, args).fetchall()


class Side:
    """One client: its account, the uploaded document and the file-import run."""

    def __init__(self, tenant: UUID, account: UUID, document: UUID, run: UUID) -> None:
        self.tenant, self.account, self.document, self.run = tenant, account, document, run


async def _side(tenant: UUID, domain: str, data: bytes, root: Path) -> Side:
    account = _make_account(tenant, domain)
    svc = service(root)
    done = await upload(svc, tenant, workspace_zip.ZIP_NAME, data, account)
    document = UUID(done["document_id"])
    assert await svc.process(tenant, document) == "indexed"
    ((run,),) = _admin(
        "SELECT id FROM account_runs WHERE account_id = %s AND finished_reason = 'file_import'",
        account,
    )
    _plant_unfilled(tenant, account, run)
    return Side(tenant, account, document, run)


def _plant_unfilled(tenant: UUID, account: UUID, run: UUID) -> None:
    """Rows for the sheets an uploaded workspace does not fill, the same in both tenants."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        tables = {t.name: t for t in tenant_tables(c)}
        for tool in ("p_search:web_search", "p_search:read_page"):
            plant(
                c,
                tables["tool_calls"],
                tenant,
                {
                    "run_id": run,
                    "tool": tool,
                    "response": Jsonb(
                        {
                            "args": {
                                "url": f"https://holdings.example/{uuid4().hex}",
                                "queries": ["q"],
                            }
                        }
                    ),
                },
            )
        for kind in SH.FILE_TABLES.values():
            for position in (1, 2):
                plant(
                    c,
                    tables["view_rows"],
                    tenant,
                    {
                        "run_id": run,
                        "account_id": account,
                        "table_kind": kind,
                        "position": position,
                        "cells": Jsonb({"name": f"row {position}"}),
                        "source_state": "no_source",
                    },
                )
        # a delivered file and its materialised tables, so the delivered-run helpers have rows.
        # The import registers the package's own files as deliverables since 2026-11-05 (exact
        # copy); a second version 1 of the same kind would break uq_deliverables_version
        registered = c.execute(
            "SELECT 1 FROM deliverables WHERE account_id = %s AND kind = 'jobs'", (account,)
        ).fetchone()
        if registered is None:
            plant(
                c,
                tables["deliverables"],
                tenant,
                {
                    "run_id": run,
                    "account_id": account,
                    "kind": "jobs",
                    "manifest_json": Jsonb({"rows": 2}),
                    "object_key": "{uuid}/{uuid}/{uuid}/deliverables/{hex64}.docx",
                    "owner_user_id": OWNER,
                },
            )
        plant(
            c,
            tables["view_materializations"],
            tenant,
            {"run_id": run, "account_id": account, "status": "ready", "owner_user_id": OWNER},
        )


@pytest_asyncio.fixture
async def sides(tenant_a: UUID, tenant_b: UUID, tmp_path: Path) -> tuple[Side, Side]:
    domain = f"holdings-{uuid4().hex[:6]}.example"
    data = _zip()
    a = await _side(tenant_a, domain, data, tmp_path / "a")
    b = await _side(tenant_b, domain, data, tmp_path / "b")
    return a, b


def _query(kind: str, key: str, run: UUID) -> SH.Query:
    return SH.parse(key, None, None, None, 0, SH.MAX_PAGE, run_id=run if kind == "file" else None)


async def _ids(s: Any, q: SH.Query, account: UUID, now: datetime) -> list[str]:
    """The row ids the sheet's own SQL returns (what the page and the export number)."""
    bound = SH.params(q, account, OWNER, now, tenant_id=await session_tenant(s))
    rows = await s.execute(text(f"SELECT s._id FROM ({SH.visible_base(q)}) s"), bound)  # noqa: S608 allow-listed
    return [str(r[0]) for r in rows.all()]


def _ids_of_tenant(tenant: UUID) -> set[str]:
    """Every row id the tenant holds in any table with an ``id`` column."""
    tables = [
        name
        for (name,) in _admin(
            "SELECT c.table_name FROM information_schema.columns c"
            " JOIN information_schema.columns k ON k.table_name = c.table_name"
            "   AND k.table_schema = c.table_schema AND k.column_name = 'id'"
            " JOIN information_schema.tables t ON t.table_name = c.table_name"
            "   AND t.table_schema = c.table_schema AND t.table_type = 'BASE TABLE'"
            " WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id'"
        )
    ]
    out: set[str] = set()
    for name in tables:
        out.update(
            str(i)
            for (i,) in _admin(f'SELECT id FROM "{name}" WHERE tenant_id = %s', tenant)  # noqa: S608 catalog names
        )
    return out


def _bare_id(value: str) -> str:
    return value.removeprefix("pull:")


async def test_every_sheet_and_count_is_isolated_between_two_clients_with_the_same_workspace(
    sides: tuple[Side, Side],
) -> None:
    """See the module docstring."""
    a, b = sides
    now = datetime.now(UTC)
    ids_of_b = _ids_of_tenant(b.tenant)
    totals: dict[str, dict[tuple[str, str], int]] = {"a": {}, "b": {}}
    uuid_checked: list[tuple[str, str]] = []

    # row level security on: each client reads its own account, and B's account reads empty
    for name, side, other in (("a", a, b), ("b", b, a)):
        async with tenant_txn(side.tenant, user=OWNER) as s:
            for kind, key in ALL_SHEETS:
                q = _query(kind, key, side.run)
                page = await SH.read(s, q, side.account, OWNER, now)
                totals[name][(kind, key)] = page["total"]
                if name == "a":
                    # row ids that are uuids (a file-shaped sheet numbers its rows by position
                    # and the coverage sheet by item key: those are checked with RLS off below)
                    got = [_bare_id(i) for i in await _ids(s, q, a.account, now)]
                    uuids = {i for i in got if len(i) == 36}
                    assert not uuids & ids_of_b, (kind, key)
                    if got and len(uuids) == len(got):
                        uuid_checked.append((kind, key))
                foreign = await SH.read(s, _query(kind, key, other.run), other.account, OWNER, now)
                assert foreign["total"] == 0 and not foreign["rows"], (name, kind, key)
            counts = (await workspace(s, side.account, OWNER, now))["counts"]
            assert counts["people"] > 0 and counts["jobs"] > 0 and counts["tech"] > 0
            assert counts["files"] > 0 and counts["sources"] > 0
            if name == "a":
                counts_a = counts
            else:
                assert counts == counts_a  # the same workspace counts the same on both sides
            # the other client's account is not found at all (the page answers 404 before any
            # count is read)
            assert await account_rows(s, now, other.account) == []

    # every sheet was read with rows on both sides, and the two sides match
    assert totals["a"] == totals["b"]
    empty = [k for k, n in totals["a"].items() if n == 0]
    assert not empty, f"sheets with no rows to isolate: {empty}"
    assert len(uuid_checked) >= 10, uuid_checked

    # row level security OFF (a superuser), A's tenant bound: B's account and run read empty
    engine = create_async_engine(ADMIN_DSN.replace("postgresql://", "postgresql+asyncpg://", 1))
    try:
        async with engine.connect() as conn, conn.begin():
            s = AsyncSession(bind=conn)
            await bind_tenant(s, a.tenant, user=OWNER, system=True)
            assert await s.scalar(text("SELECT rolbypassrls OR rolsuper FROM pg_roles"
                                       " WHERE rolname = current_user"))  # fmt: skip
            for kind, key in ALL_SHEETS:
                own = await SH.read(s, _query(kind, key, a.run), a.account, OWNER, now)
                assert own["total"] == totals["a"][(kind, key)], (kind, key)
                foreign = await SH.read(s, _query(kind, key, b.run), b.account, OWNER, now)
                assert foreign["total"] == 0 and not foreign["rows"], (kind, key)
            # a match (an Ask citation's link) narrows A's rows and never reaches B's
            match = SH.parse("jobs", None, None, None, match_col="location", match="Brentford")
            assert (await SH.read(s, match, a.account, OWNER, now))["total"] > 0
            foreign = await SH.read(s, match, b.account, OWNER, now)
            assert foreign["total"] == 0 and not foreign["rows"]
            assert not any(f["count"] for f in foreign["filters"])
            foreign_counts = (await workspace(s, b.account, OWNER, now))["counts"]
            assert not any(foreign_counts.values()), foreign_counts
            # the delivered-run helpers and the table export hold the same line
            assert await SH.delivered_run(s, a.account) == a.run
            assert await SH.delivered_run(s, b.account) is None
            assert await SH._materialised_under(s, a.run) is not None
            assert await SH._materialised_under(s, b.run) is None
            assert await SH.file_counts(s, b.account, b.run) == {}
            exported = [
                r async for r in TX.rows(s, _query("live", "jobs", b.run), b.account, OWNER, now)
            ]
            assert exported == []
            await s.close()
    finally:
        await engine.dispose()


async def test_an_internal_member_is_listed_only_to_its_uploader_and_never_read(
    sides: tuple[Side, Side],
) -> None:
    """See the module docstring."""
    a, _ = sides
    ((role, status, audience, owner),) = _admin(
        "SELECT role, status, audience, owner_user_id FROM document_members"
        " WHERE document_id = %s AND member_path = %s",
        a.document,
        INTERNAL,
    )
    assert (role, status, audience, owner) == ("owner_only", "skipped", "owner_only", UPLOADER)

    # never read: no passage, index row, table or imported row carries it
    assert _admin(
        "SELECT count(*) FROM document_passages WHERE document_id = %s"
        " AND (location->>'member' = %s OR content LIKE %s)",
        a.document,
        INTERNAL,
        "%INTERNAL-ONLY-CANARY%",
    ) == [(0,)]
    assert _admin(
        "SELECT count(*) FROM chunks WHERE content LIKE %s", "%INTERNAL-ONLY-CANARY%"
    ) == [(0,)]
    assert _admin(
        "SELECT count(*) FROM document_tables WHERE document_id = %s AND member = %s",
        a.document,
        INTERNAL,
    ) == [(0,)]
    assert _admin(
        "SELECT count(*) FROM facts WHERE value_text LIKE %s OR CAST(value_json AS text) LIKE %s",
        "%INTERNAL-ONLY-CANARY%",
        "%INTERNAL-ONLY-CANARY%",
    ) == [(0,)]
    for table, column in (
        ("research_notes", "text"),
        ("report_sections", "text"),
        ("news_events", "headline"),
    ):
        assert _admin(
            f"SELECT count(*) FROM {table} WHERE {column} LIKE %s",  # noqa: S608 fixed names
            "%INTERNAL-ONLY-CANARY%",
        ) == [(0,)], table

    # listed only to the uploader: the account's owner and another user of the client see no row
    async def listed(user: str) -> set[str]:
        async with tenant_txn(a.tenant, user=user, system=False) as s:
            rows = await s.execute(
                text("SELECT member_path FROM document_members WHERE document_id = :d"),
                {"d": a.document},
            )
            return {str(r[0]) for r in rows.all()}

    assert INTERNAL in await listed(UPLOADER)
    assert INTERNAL not in await listed(OWNER)
    assert not await listed("another-user-of-the-client")

    # and the Uploaded files sheet, read as each user (not the system scope), says the same
    async def sheet_members(user: str) -> set[str]:
        async with tenant_txn(a.tenant, user=user, system=False) as s:
            page = await SH.read(s, _query("live", "uploaded-files", a.run), a.account, user,
                                 datetime.now(UTC))  # fmt: skip
            return {str(r["cells"]["member"]) for r in page["rows"]}

    assert INTERNAL in await sheet_members(UPLOADER)
    assert INTERNAL not in await sheet_members(OWNER)
