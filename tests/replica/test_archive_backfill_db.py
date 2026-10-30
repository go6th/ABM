"""The archive backfill and the index filters on PostgreSQL: version 1 copies are rebuilt as
cleaned version 2 records keeping their capture time, records of one event are linked to the
primary source, a copy with no text is left alone, a dry run writes nothing and a second run
writes nothing. SYNTHETIC data only."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from account_intelligence import archive as LA
from account_intelligence import archive_records as AR
from account_intelligence.archive_backfill import Report, backfill_account
from account_intelligence.archive_facts import Account
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.hermetic import MemoryPayloadStore
from tests.replica.test_archive_clean import ARTICLE, PAGE, TITLE
from tests.replica.test_link_archive import _Clock, _plane_for
from tests.tracker.world import fetch, make_world

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
T0 = datetime(2026, 10, 27, 9, tzinfo=UTC)


def _system(tenant):  # type: ignore[no-untyped-def]
    return tenant_txn(tenant, system=True)


async def _legacy(archive: LA.LinkArchive, url: str, text: str, title: str) -> None:
    record = AR.news_record(
        url,
        title=title,
        summary=None,
        article_text=text,
        category="leadership_change",
        published_date="2026-09-21",
        captured_at=T0,
    )
    await archive.record(
        [await archive.entry_for_record(url, "page", record, title=title, archived_at=T0)]
    )


async def test_version_one_copies_are_rebuilt_linked_and_filterable() -> None:
    from tests.phase3.test_ws4_views import _get

    w = make_world()
    store = MemoryPayloadStore()
    archive = LA.LinkArchive(_system, w.tenant, w.account, w.run, store, _Clock(T0))
    summary_url = "https://www.agg.example/news/1"
    release_url = f"https://www.{w.domain}/newsroom/jane-roe"
    await _legacy(archive, summary_url, PAGE, TITLE)
    await _legacy(archive, release_url, ARTICLE, TITLE)
    await _legacy(archive, "https://random.example/empty", "", "Nothing stored")
    account = Account("Acme Corporation", w.domain)

    dry = Report()
    await backfill_account(archive, account, dry, apply=False)
    assert (dry.processed, dry.written, dry.ai_flagged, dry.tables_repaired) == (3, 0, 1, 1)
    assert dry.needs_refetch == ["https://random.example/empty"] and dry.failed == []
    assert dry.linked_duplicates + dry.linked_corroborations == 1 and dry.primary_matched == 1
    assert fetch("SELECT count(*) FROM link_archive WHERE account_id = %s", w.account)[0][0] == 3

    done = Report()
    await backfill_account(archive, account, done, apply=True)
    assert done.written == 2
    async with tenant_txn(w.tenant, system=True) as s:
        links, total = await LA.list_latest(s, w.account, kind=None, offset=0, limit=50)
    assert total == 3 and {link.archived_at for link in links} == {T0}  # the capture time is kept
    newest = {link.url: link for link in links}
    record = (await LA.archived_copy(store, newest[summary_url])).record
    assert (
        record
        and record["schema"] == "news-archive/2"
        and record["source_type"] == "AI-generated summary"
    )
    assert (
        record["derived_from"]["url"] == release_url and "Look First" not in record["body_markdown"]
    )
    assert (await LA.archived_copy(store, newest["https://random.example/empty"])).record[
        "schema"
    ] == "news-archive/1"

    again = Report()
    await backfill_account(archive, account, again, apply=True)
    assert again.written == 0 and again.already_current == 2

    plane = _plane_for(w, store)
    path = f"/v1/accounts/{w.domain}/archive/list"
    by_type = (await _get(plane, path, params={"source_type": "ai-generated"})).json()
    assert [i["url"] for i in by_type["items"]] == [summary_url] and by_type["total"] == 1
    item = by_type["items"][0]
    assert (item["fact_grade"], item["people"], item["published_date"]) == (
        "Lead",
        "Jane Roe; Dana R. Smith",
        "2026-09-21",
    )
    assert [
        i["url"] for i in (await _get(plane, path, params={"grade": "confirmed"})).json()["items"]
    ] == [release_url]
    assert (await _get(plane, path, params={"person": "dana", "lane": "technology"})).json()[
        "total"
    ] == 2
    assert (await _get(plane, path, params={"person": "nobody"})).json()["total"] == 0
    assert (await _get(plane, path, params={"title": "100%_"})).json()[
        "total"
    ] == 0  # a wildcard is text
    download = await _get(
        plane, f"/v1/accounts/{w.domain}/archive", params={"url": summary_url, "download": "true"}
    )
    body = download.json()
    assert body["account_domain"] == w.domain and body["record_id"].startswith(f"arc_{w.domain}_")
    assert 'filename="news-archive.json"' in download.headers["content-disposition"]
