"""The archive keeps no contact details and no secrets (leakage review 2026-10-30, F4 and F10).

* F4: a page or news copy that printed an email address or a phone number is stored, served and
  exported without them (contact details are raw payload only), and a date is not mistaken for a
  number.
* F10: an address with credentials or a signed query is stored, listed and printed without the
  credentials and the secret parameters, and the page is still found by either spelling.

GUARANTEE(test_a_page_copy_loses_its_contact_details_and_a_news_copy_too)
GUARANTEE(test_the_archive_stores_and_serves_the_address_without_secrets)
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from account_intelligence import archive as LA
from account_intelligence import archive_records as AR
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.hermetic import MemoryPayloadStore
from account_intelligence.urls import strip_secrets
from tests.tracker.world import fetch, make_world

T0 = datetime(2026, 10, 30, 9, tzinfo=UTC)
PAGE_TEXT = (
    "Contact the press office at press@synthetic-corp.example or +1 (555) 010-9999. "
    "The report was published 2026-10-30 and revised on 30.10.2026."
)


def test_a_page_copy_loses_its_contact_details_and_a_news_copy_too() -> None:
    page = AR.page_record(
        "https://synthetic-corp.example/news/1",
        PAGE_TEXT,
        title="Call +1 555 010 9999",
        captured_at=T0,
    )
    news = AR.news_record(
        "https://synthetic-corp.example/news/2",
        title="Write to a.b@synthetic-corp.example",
        summary=PAGE_TEXT,
        article_text=PAGE_TEXT,
        category="press_release",
        published_date="2026-10-30",
        captured_at=T0,
    )
    for record in (page, news):
        blob = json.dumps(record)
        assert "@synthetic-corp.example" not in blob and "555" not in blob
        assert "[redacted]" in blob
    # a date is not a phone number
    assert "2026-10-30" in page["text"] and "30.10.2026" in page["text"]
    assert news["published_date"] == "2026-10-30"


def test_a_copy_stored_before_the_rule_is_served_through_it() -> None:
    body = json.dumps(
        {
            "schema": "page-archive/1",
            "url": "https://user:pw@synthetic-corp.example/p?token=abc&page=2",
            "title": "Press",
            "text": PAGE_TEXT,
        }
    ).encode()
    link = LA.ArchivedLink(
        url="https://synthetic-corp.example/p",
        url_normalized="https://synthetic-corp.example/p",
        kind="page",
        payload_ref="k",
        format="json",
        archived_at=T0,
    )
    copy = LA.copy_from(link, body)
    assert "press@synthetic-corp.example" not in copy.text and "010-9999" not in copy.text
    assert copy.record is not None
    assert "press@" not in json.dumps(copy.record) and "token=abc" not in copy.record["url"]
    assert copy.record["url"].endswith("/p?page=2") and "pw@" not in copy.record["url"]


def test_the_release_text_of_the_workspace_zip_is_clean() -> None:
    from exports.config import load_export_config
    from exports.content import ReleaseRow
    from exports.workspace_files import release_text

    rules = load_export_config().rules.workspace
    row = ReleaseRow(
        kind="page",
        url="https://synthetic-corp.example/p?sig=zzz&a=1",
        title="Press office +1 555 010 9999",
        day=date(2026, 10, 30),
        text=PAGE_TEXT,
    )
    out = release_text(row, rules, lambda t: t)
    assert "@synthetic-corp.example" not in out and "555" not in out and "sig=zzz" not in out
    assert "url: https://synthetic-corp.example/p?a=1" in out


@pytest.mark.parametrize(
    ("raw", "shown"),
    [
        ("https://u:p@example.com/a?x=1", "https://example.com/a?x=1"),
        ("https://example.com/a?token=1&x=2&Signature=3", "https://example.com/a?x=2"),
        (
            "https://example.com/a?key=1&api_key=2&access_token=3&password=4",
            "https://example.com/a",
        ),
        ("https://example.com/a?X-Amz-Signature=1&id=7#top", "https://example.com/a?id=7#top"),
        ("https://example.com/jobs?gh_jid=77&page=2", "https://example.com/jobs?gh_jid=77&page=2"),
    ],
)
def test_strip_secrets_keeps_everything_else(raw: str, shown: str) -> None:
    assert strip_secrets(raw) == shown


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_archive_stores_and_serves_the_address_without_secrets() -> None:
    w = make_world()
    store = MemoryPayloadStore()
    archive = LA.LinkArchive(
        tenant_txn, w.tenant, w.account, w.run, store, SimpleNamespace(now=lambda: T0)
    )
    secret = "https://reader:hunter2@synthetic-corp.example/report?token=abc123&page=2"
    await archive.put_text(secret, "page", "A public report.", title="Report")
    stored = fetch("SELECT url, url_normalized FROM link_archive WHERE account_id = %s", w.account)
    assert len(stored) == 1
    assert "hunter2" not in stored[0][0] and "abc123" not in stored[0][0]
    assert "abc123" not in stored[0][1]
    async with tenant_txn(w.tenant) as s:
        # found by the address as given, and by the address as shown
        by_secret = await LA.lookup(s, w.account, [secret])
        shown = "https://synthetic-corp.example/report?page=2"
        by_shown = await LA.lookup(s, w.account, [shown])
        assert by_secret[secret].url == shown and by_shown[shown].url == shown
        listed, _total = await LA.list_latest(s, w.account, kind=None, offset=0, limit=10)
        assert [x.url for x in listed] == [shown]
