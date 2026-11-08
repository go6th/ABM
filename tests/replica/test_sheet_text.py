"""The words of the deep-dive tables (2026-11-07 client web audit): the scope lines and footnotes
name no database table and carry no "click a heading" hint, a scope line never ends in a stray
space, and the Calls made sheet shows each call as plain words (a tool name a person reads and
its arguments as "queries: a | b" instead of a code and JSON). SYNTHETIC rows only."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import sheets as SH
from tests.conftest import ADMIN_DSN, _make_account

OWNER = "test-owner"  # the account's owner (tests.conftest._make_account)
# a word the database is made of, as it would read in a table name or in double backticks
_INTERNAL = re.compile(
    r"``|tool_calls|pull_log_calls|research_notes|document_members|org_connections"
    r"|platform_teams|report_sections|_[a-z]+_[a-z]+",
)


def test_no_scope_or_footnote_names_a_table_or_tells_the_reader_to_click() -> None:
    for key, sheet in SH.SHEETS.items():
        for text in (sheet.scope, sheet.footnote):
            assert not _INTERNAL.search(text), (key, text)
            assert "column heading" not in text.lower(), (key, text)
        assert sheet.scope == sheet.scope.strip() and sheet.scope.endswith("."), key


def test_a_scope_built_with_the_old_hint_ends_without_a_stray_space() -> None:
    sheet = SH.Sheet(
        key="x", title="X", file="X.xlsx", scope=f"One sentence. {SH.SORT_HINT}", base="",
        cols=(), filters=(), default_sort="x",
    )  # fmt: skip
    assert sheet.scope == "One sentence."


def test_a_footnote_never_repeats_the_scope_line() -> None:
    for key, sheet in SH.SHEETS.items():
        assert not sheet.footnote or sheet.footnote.strip() != sheet.scope, key


def test_every_tool_the_research_stores_has_a_plain_label() -> None:
    for code in ("web_search", "read_page", "scrape_job_page", "news_events", "job_openings"):
        assert code in SH.TOOL_LABELS
    assert all(label[0].isupper() and "_" not in label and ":" not in label
               for label in SH.TOOL_LABELS.values())  # fmt: skip


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_calls_made_shows_plain_tool_names_and_readable_arguments(tenant_a: UUID) -> None:
    account = _make_account(tenant_a, f"calls-{uuid4().hex[:6]}.example")
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        run = c.execute(
            "INSERT INTO account_runs (tenant_id, account_id, graph_version, budget_json,"
            " reserved_credits, idempotency_key) VALUES (%s, %s, 'test', '{}', 1, %s)"
            " RETURNING id",
            (tenant_a, account, "k-" + uuid4().hex[:10]),
        ).fetchone()[0]
        calls = (
            ("p_search:web_search", {"queries": ["acme pricing", "acme cloud"], "objective": "x"}),
            ("p_search:read_page", {"url": "https://acme.example/careers"}),
            ("p_signals:brand_new_thing", {"limit": 5}),
        )
        for tool, args in calls:
            c.execute(
                "INSERT INTO tool_calls (tenant_id, run_id, tool, args_hash, response,"
                " cost_usd, cost_source) VALUES (%s, %s, %s, %s, %s, 0, 'documented')",
                (tenant_a, run, tool, uuid4().hex, Jsonb({"args": args, "record_count": 3})),
            )
    q = SH.parse("calls", None, None, None, 0, SH.MAX_PAGE)
    async with tenant_txn(tenant_a, user=OWNER) as s:
        page = await SH.read(s, q, account, OWNER, datetime.now(UTC))
    by_tool = {r["cells"]["tool"]: r["cells"]["parameters"] for r in page["rows"]}
    assert by_tool == {
        "Web search": "objective: x; queries: acme pricing | acme cloud",
        "Page read": "url: https://acme.example/careers",
        "Brand new thing": "limit: 5",
    }
    shown = " ".join(str(v) for r in page["rows"] for v in r["cells"].values())
    assert "p_search" not in shown and "{" not in shown and '"' not in shown
