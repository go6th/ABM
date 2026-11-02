"""Ask reads the pages research read, through the archive routes' own guard (Ask redesign 3c,
2026-11-02). SYNTHETIC data only.

GUARANTEE(test_ask_reads_research_pages_through_the_archive_guard)
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import psycopg
import pytest
import pytest_asyncio

from account_intelligence import archive as LA
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.hermetic import MemoryPayloadStore
from account_intelligence.replica import archive_tools as ARCH
from account_intelligence.replica import tools as TL
from documents.tables import QueryError

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
T0 = datetime(2026, 10, 30, tzinfo=UTC)
LONG = "Platform migration. " * 3000  # 60,000 characters
PAGES = {
    "https://news.example/launch": ("page", "Acme launches a data platform.", "Acme launches"),
    "https://jobs.example/arch-1": (
        "posting",
        "Salesforce Architect role.",
        "Salesforce Architect",
    ),
    "https://news.example/long": ("page", LONG, "A long read"),
    "https://news.example/secret": ("page", "Nothing here, but the title hides it.", "HIDDEN item"),
}


class Guard:
    """Hides any copy titled HIDDEN, blanks the word "forbidden", owns every payload."""

    def hides(self, *texts: str | None, url: str | None = None) -> bool:
        return any("HIDDEN" in (t or "") for t in texts)

    def scrub(self, value: Any) -> tuple[Any, list[str]]:
        held: list[str] = []

        def walk(v: Any, path: str = "") -> Any:
            if isinstance(v, str):
                if "forbidden" in v:
                    held.append(path)
                    return ""
                return v
            if isinstance(v, dict):
                return {k: walk(x, f"{path}.{k}" if path else k) for k, x in v.items()}
            if isinstance(v, list):
                return [walk(x, path) for x in v]
            return v

        return walk(value), held

    def owned(self, ref: str) -> bool:
        return True


async def _guard(_account: UUID) -> Guard:
    return Guard()


@pytest_asyncio.fixture(scope="module")
async def archived(world: dict[str, Any]) -> dict[str, Any]:
    from tests.conftest import ADMIN_DSN

    tenant = world["a"]
    with psycopg.connect(ADMIN_DSN) as c:
        rows = c.execute(
            "SELECT id, domain, coalesce(name, domain) FROM accounts WHERE tenant_id = %s"
            " ORDER BY domain LIMIT 2",
            (tenant,),
        ).fetchall()
    (a1, d1, n1), (a2, d2, n2) = ((UUID(str(r[0])), str(r[1]), str(r[2])) for r in rows)
    store = MemoryPayloadStore()
    archive = LA.LinkArchive(tenant_txn, tenant, a1, None, store, SimpleNamespace(now=lambda: T0))
    for url, (kind, text, title) in PAGES.items():
        await archive.put_text(url, kind, text, title=title)
    return {
        "tenant": tenant,
        "accounts": {a1: (d1, n1), a2: (d2, n2)},
        "store": store,
        "a1": a1,
        "n1": n1,
    }


def _tools(a: dict[str, Any]) -> ARCH.ArchiveTools:
    return ARCH.ArchiveTools(lambda: tenant_txn(a["tenant"]), a["accounts"], a["store"], _guard)


async def test_ask_reads_research_pages_through_the_archive_guard(archived: dict[str, Any]) -> None:
    tools = _tools(archived)
    listing = await tools.run("list_research_pages", {"account": archived["n1"]})
    urls = {p["url"] for p in listing["pages"]}
    # the copy titled HIDDEN is hidden, and counted; the others list
    assert urls == set(PAGES) - {"https://news.example/secret"}
    assert listing["total"] == 4 and listing["hidden"] == 1
    only_posts = await tools.run(
        "list_research_pages", {"account": archived["n1"], "kind": "posting"}
    )
    assert [p["title"] for p in only_posts["pages"]] == ["Salesforce Architect"]
    by_title = await tools.run(
        "list_research_pages", {"account": archived["n1"], "filters": {"title": "launch"}}
    )
    assert [p["url"] for p in by_title["pages"]] == ["https://news.example/launch"]

    page = await tools.run(
        "read_research_page", {"account": archived["n1"], "url": "https://news.example/launch"}
    )
    assert page["text"] == "Acme launches a data platform." and page["next_start"] is None
    # a long page reads in parts, each continuing where the last stopped, until all is read
    got, start, parts = "", 0, 0
    while start is not None:
        piece = await tools.run(
            "read_research_page",
            {"account": archived["n1"], "url": "https://news.example/long", "start": start},
        )
        got, start, parts = got + piece["text"], piece["next_start"], parts + 1
    assert got == LONG and parts == 3 and piece["total_chars"] == len(LONG)
    # a hidden copy and an unknown address are the same "not found"
    for url in ("https://news.example/secret", "https://nowhere.example/x"):
        with pytest.raises(QueryError) as error:
            await tools.run("read_research_page", {"account": archived["n1"], "url": url})
        assert error.value.code == "not_found"
    assert tools.reads == 4  # the launch page and three parts of the long one


async def test_the_account_is_named_by_name_or_domain_and_only_those_in_scope(
    archived: dict[str, Any],
) -> None:
    tools = _tools(archived)
    with pytest.raises(QueryError) as needed:
        await tools.run("list_research_pages", {})  # two accounts in scope: name one
    assert needed.value.code == "account_needed"
    with pytest.raises(QueryError) as unknown:
        await tools.run("list_research_pages", {"account": "not-an-account.example"})
    assert unknown.value.code == "unknown_account"
    domain = archived["accounts"][archived["a1"]][0]
    assert (await tools.run("list_research_pages", {"account": domain}))["total"] == 4
    # a turn with one account in scope needs no name
    one = ARCH.ArchiveTools(
        lambda: tenant_txn(archived["tenant"]),
        {archived["a1"]: archived["accounts"][archived["a1"]]},
        archived["store"],
        _guard,
    )
    assert (await one.run("list_research_pages", {}))["total"] == 4
    with pytest.raises(QueryError) as bad:
        await one.run("list_research_pages", {"filters": {"salary": "x"}})
    assert bad.value.code == "bad_filters"


async def test_a_failing_guard_holds_the_archive_back_and_the_tools_are_offered_on_two_routes(
    archived: dict[str, Any],
) -> None:
    async def broken(_account: UUID) -> Guard:
        raise RuntimeError("settings do not validate")

    closed = ARCH.ArchiveTools(
        lambda: tenant_txn(archived["tenant"]), archived["accounts"], archived["store"], broken
    )
    with pytest.raises(QueryError) as error:
        await closed.run("list_research_pages", {"account": archived["n1"]})
    assert error.value.code == "archive_unavailable"

    access = TL.AccessTools(
        lambda: tenant_txn(archived["tenant"]), [], [], None, archive=_tools(archived)
    )
    names = {r: {t["name"] for t in access.offered(r)} for r in ("tabular", "prose", "mixed")}
    assert set(ARCH.NAMES) <= names["prose"] and set(ARCH.NAMES) <= names["mixed"]
    assert not set(ARCH.NAMES) & names["tabular"]
    out = await access.run("list_research_pages", {"account": archived["n1"]})
    assert out["total"] == 4 and access.stats()["pages_read"] == 0 and access.data_calls == 1
    bare = TL.AccessTools(lambda: tenant_txn(archived["tenant"]), [], [], None)
    assert not set(ARCH.NAMES) & {t["name"] for t in bare.offered("mixed")}
    assert (await bare.run("list_research_pages", {}))["error"]["code"] == "unknown_tool"


def test_a_record_naming_an_excluded_person_in_a_list_is_found_as_the_archive_route_finds_it() -> (
    None
):
    """Audit 2026-11-02: Ask read top-level strings only; the route also reads the dicts inside
    lists (a news record's people, a posting's platform sentences)."""
    record = {
        "title": "Acme names a CIO",
        "people": [{"name": "Excluded Person", "role": "CIO"}, "Jane Doe"],
        "platform_sentences": [{"platform": "Salesforce", "text": "Built by Excluded Person."}],
        "count": 3,
    }
    found = ARCH.record_texts(record)
    assert "Excluded Person" in found and "Jane Doe" in found
    assert any("Built by Excluded Person" in t for t in found) and "Acme names a CIO" in found
    assert ARCH.record_texts(None) == []


async def test_bad_filters_are_an_error_not_a_crash_and_pages_continue_by_next_offset(
    archived: dict[str, Any],
) -> None:
    tools = _tools(archived)
    for bad in (True, 5, ["title"]):
        with pytest.raises(QueryError) as error:
            await tools.run("list_research_pages", {"account": archived["n1"], "filters": bad})
        assert error.value.code == "bad_filters"
    first = await tools.run("list_research_pages", {"account": archived["n1"], "limit": 2})
    # one of the four is hidden, but it still takes a slot of the numbering
    assert first["next_offset"] == 2 and first["total"] == 4
    last = await tools.run(
        "list_research_pages",
        {"account": archived["n1"], "limit": 2, "offset": first["next_offset"]},
    )
    assert last["next_offset"] is None
    seen = {p["url"] for p in first["pages"]} | {p["url"] for p in last["pages"]}
    assert seen == set(PAGES) - {"https://news.example/secret"}  # nothing skipped, nothing twice


async def test_the_rate_limit_applies_the_policy_is_built_once_and_a_term_cannot_hide_in_a_seam(
    archived: dict[str, Any],
) -> None:
    built: list[UUID] = []

    async def counting(account: UUID) -> Guard:
        built.append(account)
        return Guard()

    asked: list[int] = []

    async def limit() -> None:
        asked.append(1)
        if len(asked) > 2:
            raise RuntimeError("429")

    tools = ARCH.ArchiveTools(
        lambda: tenant_txn(archived["tenant"]),
        archived["accounts"],
        archived["store"],
        counting,
        limit,
    )
    await tools.run("list_research_pages", {"account": archived["n1"]})
    await tools.run("list_research_pages", {"account": archived["n1"]})
    assert len(built) == 1  # one policy build for the turn, not one per call
    with pytest.raises(QueryError) as limited:
        await tools.run("list_research_pages", {"account": archived["n1"]})
    assert limited.value.code == "rate_limited"

    # a forbidden term straddling the 20,000-character seam is caught: the whole text is scrubbed
    a2 = next(a for a in archived["accounts"] if a != archived["a1"])
    seam = "a" * (ARCH.READ_DEFAULT_CHARS - 4) + "forbidden" + "b" * 100
    archive = LA.LinkArchive(
        tenant_txn, archived["tenant"], a2, None, archived["store"], SimpleNamespace(now=lambda: T0)
    )
    await archive.put_text("https://news.example/seam", "page", seam, title="Seam page")
    page = await _tools(archived).run(
        "read_research_page",
        {"account": archived["accounts"][a2][1], "url": "https://news.example/seam"},
    )
    assert page["text"] == "" and "text" in page["withheld"]
