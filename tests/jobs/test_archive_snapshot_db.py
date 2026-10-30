"""The structured posting copy on PostgreSQL: ``PgPostingRepo.save_run`` archives each posting as a
``posting-archive/1`` JSON record, an unchanged posting seen again writes nothing new, changed
content writes a new copy beside the old one (the newest wins), older ``format text`` copies stay
readable, and the index lists the newest copy per address, paged. SYNTHETIC data only."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from account_intelligence import archive as LA
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.hermetic import MemoryPayloadStore
from jobs import snapshot as SN
from jobs.repo import PgPostingRepo
from jobs.stage import run_jobs
from tests.fakes.jobs import Corpus, FakeRunInput, Recorder
from tests.jobs.conftest import build
from tests.tracker.world import World, fetch, make_world

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


class _Store:
    """The payload store the repo reads texts from and writes copies to: the pipeline's stored
    texts plus whatever the archive adds."""

    def __init__(self, rec: Recorder) -> None:
        self.rec = rec
        self.objects = MemoryPayloadStore()

    async def get(self, key: str) -> bytes:
        if key in self.rec.texts:
            return self.rec.texts[key].encode()
        return await self.objects.get(key)

    async def put(self, key: str, body: bytes) -> str:
        return await self.objects.put(key, body)


async def _run(w: World) -> tuple[Any, Recorder]:
    corpus = Corpus()
    pipeline, *_ = build(corpus)
    rec = Recorder()
    repo = PgPostingRepo(tenant_txn, w.tenant, w.account)  # the text copies of the original build
    result = await run_jobs(
        str(w.run), pipeline, repo, rec, rec, rec, FakeRunInput(corpus.lanes_off, corpus.as_of)
    )
    return result, rec


async def _archive(repo: PgPostingRepo, w: World, result: Any) -> int:
    entries = await repo._archive_entries(result)
    async with tenant_txn(w.tenant) as s:
        return await LA.record(s, w.tenant, w.account, w.run, entries)


def _rows(w: World, fmt: str) -> list[tuple[Any, ...]]:
    return fetch(
        "SELECT url, payload_ref FROM link_archive WHERE account_id = %s AND format = %s"
        " AND kind = 'posting' ORDER BY id",
        w.account,
        fmt,
    )


async def test_each_posting_is_archived_as_a_structured_record() -> None:
    w = make_world()
    result, rec = await _run(w)
    store = _Store(rec)
    repo = PgPostingRepo(tenant_txn, w.tenant, w.account, store)
    with_text = [p for p in result.postings if p.text_ref]
    assert with_text
    written = await _archive(repo, w, result)
    assert written == len(with_text) and len(_rows(w, "json")) == len(with_text)
    # the original text copies (older build) are still there, and still readable
    assert len(_rows(w, "text")) == len(with_text)
    posting = with_text[0]
    async with tenant_txn(w.tenant) as s:
        found = (await LA.lookup(s, w.account, [posting.url]))[posting.url]
    # the newest copy of the address is the structured one, a neutral content-addressed key
    assert found.format == "json" and found.payload_ref.startswith(
        f"tenants/{w.tenant}/accounts/{w.account}/archive/"
    )
    copy = await LA.archived_copy(store, found)
    assert copy.record is not None and copy.record["schema"] == SN.SCHEMA
    assert copy.record["url"] == posting.url and copy.record["title"] == posting.title
    assert copy.record["status_at_capture"] == posting.status
    assert copy.text == copy.record["description"] and copy.text
    assert [x["text"] for x in copy.record["platform_sentences"]] == [
        s.text for s in posting.sentences
    ]
    body = json.loads(await store.get(found.payload_ref))
    assert "S-" not in body["source"] and body["source"] == "6th agents"
    old = fetch(
        "SELECT payload_ref FROM link_archive WHERE account_id = %s AND url = %s"
        " AND format = 'text'",
        w.account,
        posting.url,
    )
    text_copy = await LA.archived_copy(
        store,
        LA.ArchivedLink(
            posting.url, posting.url, "posting", str(old[0][0]), "text", found.archived_at, None
        ),
    )
    assert text_copy.record is None and text_copy.text == rec.texts[posting.text_ref or ""]


async def test_unchanged_is_one_copy_changed_content_is_a_new_copy_and_the_newest_wins() -> None:
    w = make_world()
    result, rec = await _run(w)
    store = _Store(rec)
    repo = PgPostingRepo(tenant_txn, w.tenant, w.account, store)
    first = await _archive(repo, w, result)
    assert await _archive(repo, w, result) == first  # recorded again: the same rows, none new
    assert len(_rows(w, "json")) == first
    target = next(p for p in result.postings if p.text_ref and p.status == "open")
    closed = target.model_copy(update={"status": "closed"})
    later = replace(result, postings=tuple(closed if p is target else p for p in result.postings))
    await _archive(repo, w, later)
    mine = fetch(
        "SELECT payload_ref FROM link_archive WHERE account_id = %s AND url = %s"
        " AND format = 'json' ORDER BY id",
        w.account,
        target.url,
    )
    assert len(mine) == 2 and mine[0] != mine[1]  # the older copy stays beside the new one
    assert len(_rows(w, "json")) == first + 1  # every other posting kept its one copy
    async with tenant_txn(w.tenant) as s:
        newest = (await LA.lookup(s, w.account, [target.url]))[target.url]
    assert newest.payload_ref == mine[1][0]
    copy = await LA.archived_copy(store, newest)
    assert copy.record is not None and copy.record["status_at_capture"] == "closed"
    older = json.loads(await store.get(str(mine[0][0])))
    assert older["status_at_capture"] == target.status  # what it said when first captured


async def test_the_guard_withholds_a_field_of_the_stored_copy() -> None:
    w = make_world()
    result, rec = await _run(w)
    store = _Store(rec)
    needle = next(p for p in result.postings if p.text_ref).title

    def guard(text: str) -> tuple[str, bool]:
        return text, text == needle

    repo = PgPostingRepo(tenant_txn, w.tenant, w.account, store, guard)
    await _archive(repo, w, result)
    async with tenant_txn(w.tenant) as s:
        links = (await LA.list_latest(s, w.account, kind="posting", offset=0, limit=200))[0]
    copies = [await LA.archived_copy(store, link) for link in links if link.format == "json"]
    held = [c for c in copies if c.record and "title" in c.record.get("withheld", [])]
    assert held and all(c.record and c.record["title"] == "" for c in held)


async def test_the_index_lists_the_newest_copy_per_address_paged() -> None:
    w = make_world()
    result, rec = await _run(w)
    store = _Store(rec)
    repo = PgPostingRepo(tenant_txn, w.tenant, w.account, store)
    await _archive(repo, w, result)
    distinct = {p.url for p in result.postings if p.text_ref}
    async with tenant_txn(w.tenant) as s:
        page1, total = await LA.list_latest(s, w.account, kind="posting", offset=0, limit=3)
        page2, _ = await LA.list_latest(s, w.account, kind="posting", offset=3, limit=3)
        nothing, none_total = await LA.list_latest(s, w.account, kind="profile", offset=0, limit=3)
        capped, _ = await LA.list_latest(s, w.account, kind=None, offset=0, limit=10_000)
    assert (
        total == len(distinct)
        and len(page1) == 3
        and not set(x.url for x in page1) & set(x.url for x in page2)
    )
    assert nothing == [] and none_total == 0
    assert len(capped) == len(distinct) <= LA.MAX_LIST  # one row per address, whatever the copies
    stamps = [x.archived_at for x in page1 + page2]
    assert stamps == sorted(stamps, reverse=True)  # newest capture first
    items = await LA.list_items(store, page1)
    by_url = {p.url: p for p in result.postings}
    for item in items:
        assert item.kind == "posting" and item.status_at_capture == by_url[item.url].status
        assert item.location == (by_url[item.url].location or None)
    # a body that cannot be read lists blank, the address still lists
    gone = LA.ArchivedLink("https://gone.example/x", "x", "posting", "t/none", "json", stamps[0])
    blank = (await LA.list_items(store, [gone]))[0]
    assert blank.url == "https://gone.example/x" and blank.status_at_capture is None


async def test_a_posting_copy_stores_its_index_fields_beside_the_row() -> None:
    """Fix wave C (wave B leftover 1): without ``meta`` the archive index read up to 50 posting
    bodies per page; the row carries status, location and schema itself."""
    w = make_world()
    result, rec = await _run(w)
    repo = PgPostingRepo(tenant_txn, w.tenant, w.account, _Store(rec))
    await _archive(repo, w, result)
    rows = fetch(
        "SELECT meta FROM link_archive WHERE account_id = %s AND format = 'json'"
        " AND kind = 'posting'",
        w.account,
    )
    assert rows and all(r[0] and r[0].get("schema") == SN.SCHEMA for r in rows)
    assert all(r[0].get("status_at_capture") for r in rows)
