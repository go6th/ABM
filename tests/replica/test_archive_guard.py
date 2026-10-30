"""The client archive routes keep what the client excluded, the other accounts and the provider
names out, never serve a profile copy, read no body for the index, cap what they answer and
bring the newest status back (security review 2026-10-29: F1, F5, F6, F9, F12, F13, F15, F21).
SYNTHETIC data only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import errors

from account_intelligence import archive as LA
from account_intelligence import archive_records as AR
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.hermetic import MemoryPayloadStore
from account_intelligence.replica import sheets as SH
from tenancy import settings as TS
from tests.phase3.test_ws4_views import _get
from tests.replica.test_link_archive import PAGE, PROFILE, T0, _archive, _plane_for
from tests.tracker.world import World, fetch, make_world

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
JANE = "https://news.synthetic-wire.example/jane-doe-named-cio"
NEUTRAL = "https://news.synthetic-wire.example/quarterly-update"
BLOCKED = "https://www.blocked-co.example/about"


async def _exclude(
    w: World, *, people: tuple[str, ...] = (), companies: tuple[str, ...] = ()
) -> None:
    async with tenant_txn(w.tenant) as s:
        current = await TS.current(s)
        changed = current.settings.model_copy(
            update={"exclusions": TS.Exclusions(companies=companies, people=people)}
        )
        await TS.save(
            s, changed, actor="admin-1", expected_version=current.version, now=datetime.now(UTC)
        )


async def _page(w: World, store: MemoryPayloadStore, url: str, title: str, text: str) -> None:
    archive = _archive(w, store)
    record = AR.page_record(url, text, title=title, captured_at=T0)
    entry = await archive.entry_for_record(url, "page", record, title=title, archived_at=T0)
    await archive.record([entry])


# ---- F1: profile copies are internal; the tenant's exclusions apply to everything -------------


async def test_a_profile_copy_is_never_listed_or_served_and_never_linked() -> None:
    w = make_world()
    store = MemoryPayloadStore()
    await _archive(w, store).put_text(PROFILE, "profile", "Jane Doe\nCIO", title="Jane Doe")
    await _page(w, store, PAGE, "News", "A page.")
    plane = _plane_for(w, store)
    base = f"/v1/accounts/{w.domain}/archive"
    assert (await _get(plane, base, params={"url": PROFILE})).status_code == 404
    listing = (await _get(plane, base + "/list")).json()
    assert [i["url"] for i in listing["items"]] == [PAGE] and listing["total"] == 1
    only = (await _get(plane, base + "/list", params={"kind": "profile"})).json()
    assert only["items"] == [] and only["total"] == 0
    # the matcher of the people pipeline still reads it (an internal input)
    async with tenant_txn(w.tenant) as s:
        assert [p.url for p in await LA.profile_links(s, w.account)] == [PROFILE]
        # and no table row links to it
        rows: list[dict[str, Any]] = [
            {"n": 1, "cells": {}, "links": {"url": PROFILE}, "link": PAGE}
        ]
        await SH.attach_archives(s, w.account, rows, w.domain)
    assert list(rows[0]["archives"]) == ["link"]


async def test_the_tenant_exclusions_hold_on_the_archive_routes() -> None:
    w = make_world()
    store = MemoryPayloadStore()
    await _page(w, store, JANE, "Synthetic Corp names its CIO", "Jane Doe joins as CIO.")
    await _page(w, store, NEUTRAL, "Jane Doe leaves", "Quarterly numbers.")  # title names her
    await _page(w, store, NEUTRAL + "-2", "Quarterly update", "As told by Jane Doe, the CIO.")
    await _page(w, store, BLOCKED, "About us", "A page of an excluded company.")
    await _page(w, store, PAGE, "Neutral news", "Nothing to see.")
    plane = _plane_for(w, store)
    base = f"/v1/accounts/{w.domain}/archive"
    # before any exclusion everything is served
    assert (await _get(plane, base + "/list")).json()["total"] == 5
    assert (await _get(plane, base, params={"url": JANE})).status_code == 200
    # the exclusion is added AFTER the research: it applies to what is already archived
    await _exclude(w, people=("Jane Doe",), companies=("blocked-co.example",))
    listing = (await _get(plane, base + "/list")).json()
    # the index holds titles, never text: a page that names her only in its body stays listed
    # (its copy is refused when opened, below)
    assert {i["url"] for i in listing["items"]} == {PAGE, NEUTRAL + "-2"}
    shown = {i["url"] for i in listing["items"]}
    assert JANE not in shown and NEUTRAL not in shown and BLOCKED not in shown
    for url in (JANE, NEUTRAL, NEUTRAL + "-2", BLOCKED):
        assert (await _get(plane, base, params={"url": url})).status_code == 404, url
    assert (await _get(plane, base, params={"url": PAGE})).status_code == 200


async def test_an_excluded_profile_address_hides_the_pages_that_name_it() -> None:
    w = make_world()
    store = MemoryPayloadStore()
    await _page(
        w, store, JANE, "Leadership", "Profile: https://www.linkedin.com/in/jane-doe-7 is listed."
    )
    await _page(w, store, PAGE, "Neutral news", "Another profile: linkedin.com/in/jane-doe-70.")
    await _exclude(w, people=("https://www.linkedin.com/in/jane-doe-7",))
    plane = _plane_for(w, store)
    base = f"/v1/accounts/{w.domain}/archive"
    assert (await _get(plane, base, params={"url": JANE})).status_code == 404
    # a longer address is another person's
    assert (await _get(plane, base, params={"url": PAGE})).status_code == 200


# ---- F6: other accounts and excluded companies are forbidden terms ---------------------------


async def test_a_copy_that_names_another_account_of_the_client_is_withheld_per_field() -> None:
    w = make_world()
    store = MemoryPayloadStore()
    with psycopg.connect(__import__("tests.conftest", fromlist=["ADMIN_DSN"]).ADMIN_DSN) as c:
        c.execute(
            "INSERT INTO accounts (tenant_id, owner_user_id, domain, name)"
            " VALUES (%s, 'someone-else', %s, 'Zenith Rival')",
            (w.tenant, f"zenith-{uuid4().hex[:6]}.example"),
        )
    await _page(w, store, PAGE, "A comparison", "Our view versus Zenith Rival in the market.")
    await _page(w, store, NEUTRAL, "Zenith Rival wins", "Plain text.")
    await _exclude(w, companies=("blocked-co.example",))
    await _page(w, store, JANE, "Partners", "We also work with blocked-co.example on delivery.")
    plane = _plane_for(w, store)
    base = f"/v1/accounts/{w.domain}/archive"
    one = (await _get(plane, base, params={"url": PAGE})).json()
    assert one["text"] == "" and "text" in one["withheld"] and one["title"] == "A comparison"
    two = (await _get(plane, base, params={"url": NEUTRAL})).json()
    assert two["title"] == "" and two["text"] == "Plain text."
    three = (await _get(plane, base, params={"url": JANE})).json()
    assert three["text"] == "" and "text" in three["withheld"]
    rows = (await _get(plane, base + "/list")).json()["items"]
    titles = {i["url"]: i["title"] for i in rows}
    assert titles[NEUTRAL] == "" and titles[PAGE] == "A comparison"


# ---- F5: an archive address for a provider link is dropped with the link ---------------------


async def test_the_archive_address_of_a_provider_link_is_dropped_with_the_link() -> None:
    from documents.gate import provider_hosts

    host = sorted(provider_hosts())[0]
    url = f"https://{host}/v1/tasks/abc"
    w = make_world()
    store = MemoryPayloadStore()
    await _page(w, store, url, "Result", "Some text.")
    await _page(w, store, PAGE, "News", "A page.")
    rows: list[dict[str, Any]] = [
        {"n": 1, "cells": {}, "links": {"src": url}, "link": url},
        {"n": 2, "cells": {}, "links": {}, "link": PAGE},
    ]
    async with tenant_txn(w.tenant) as s:
        await SH.attach_archives(s, w.account, rows, w.domain)
    assert rows[0]["archives"] == {}  # never offered
    assert list(rows[1]["archives"]) == ["link"]
    # whatever builds a row's archive addresses, the cell guard drops one that names the provider
    rows[0]["archives"] = {"src": {"href": f"/accounts/x/archive?url={url}", "archived_at": "t"}}
    rows[0]["archives"]["src"]["href"] = (
        "/accounts/x/archive?url=https%3A%2F%2F" + host + "%2Fv1%2Ftasks%2Fabc"
    )
    SH.scrub_rows(rows)
    assert rows[0]["archives"] == {}
    plane = _plane_for(w, store)
    base = f"/v1/accounts/{w.domain}/archive"
    assert (await _get(plane, base, params={"url": url})).status_code == 404
    assert [i["url"] for i in (await _get(plane, base + "/list")).json()["items"]] == [PAGE]


# ---- F12: no body is read for the index; the copy answers a capped text ---------------------


class _CountingStore(MemoryPayloadStore):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    async def get(self, key: str) -> bytes:
        self.reads += 1
        return await super().get(key)


async def test_the_index_reads_the_fields_beside_the_row_not_the_bodies() -> None:
    w = make_world()
    store = _CountingStore()
    when = T0
    entries = [
        LA.ArchiveEntry(
            f"https://jobs.synthetic-acme.example/j{i}",
            "posting",
            f"tenants/{w.tenant}/accounts/{w.account}/archive/{i:064x}.json.gz",
            when + timedelta(minutes=i),
            title=f"Posting {i}",
            meta={"status_at_capture": "closed", "location": f"Town {i}", "requisition": "R-1"},
        )
        for i in range(6)
    ]
    async with tenant_txn(w.tenant) as s:
        await LA.record(s, w.tenant, w.account, w.run, entries)
    plane = _plane_for(w, store)
    page = (
        await _get(plane, f"/v1/accounts/{w.domain}/archive/list", params={"limit": "4"})
    ).json()
    assert store.reads == 0
    assert page["total"] == 6 and page["next_offset"] == 4
    assert (
        page["items"][0]["location"] == "Town 5"
        and page["items"][0]["status_at_capture"] == "closed"
    )
    # a row of an older research (no fields stored) reads its body, at most LEGACY_BODY_READS
    assert (
        await _get(plane, f"/v1/accounts/{w.domain}/archive/list", params={"offset": "100001"})
    ).status_code == 422


async def test_the_copy_answers_a_capped_text_and_the_download_gives_all_of_it() -> None:
    w = make_world()
    store = MemoryPayloadStore()
    big = "x" * (LA.MAX_COPY_TEXT + 500)
    await _archive(w, store).put_text(PAGE, "page", big, title="Long page")
    plane = _plane_for(w, store)
    path = f"/v1/accounts/{w.domain}/archive"
    cut = (await _get(plane, path, params={"url": PAGE})).json()
    assert len(cut["text"]) == LA.MAX_COPY_TEXT and cut["truncated"] is True
    whole = await _get(plane, path, params={"url": PAGE, "download": "true"})
    assert len(whole.json()["text"]) == len(big) and whole.json()["truncated"] is False
    # the JSON download is an audited export (not a plain read)
    actions = [
        r[0] for r in fetch("SELECT action FROM audit_events WHERE tenant_id = %s", w.tenant)
    ]
    assert "export.download_archive" in actions


# ---- F9: a legacy plain-text copy is served without contact details ---------------------------


async def test_a_legacy_text_copy_is_served_without_email_or_phone() -> None:
    w = make_world()
    store = MemoryPayloadStore()
    key = f"tenants/{w.tenant}/accounts/{w.account}/archive/{'a' * 64}.txt"
    await store.put(key, b"Apply to jobs@synthetic-acme.example or call +1 415 555 0100 today.")
    async with tenant_txn(w.tenant) as s:
        await LA.record(
            s, w.tenant, w.account, w.run, [LA.ArchiveEntry(PAGE, "posting", key, T0, "text")]
        )
    body = (
        await _get(_plane_for(w, store), f"/v1/accounts/{w.domain}/archive", params={"url": PAGE})
    ).json()
    assert (
        "jobs@" not in body["text"] and "555" not in body["text"] and "[redacted]" in body["text"]
    )


# ---- F13: the newest status wins when content returns to an earlier state --------------------


async def test_a_page_that_returns_to_earlier_content_is_the_newest_copy_again() -> None:
    w = make_world()
    store = MemoryPayloadStore()
    url = "https://jobs.synthetic-acme.example/p9"
    for text, at in (
        ("open text", T0),
        ("closed text", T0 + timedelta(days=1)),
        ("open text", T0 + timedelta(days=2)),
    ):
        archive = _archive(w, store, at)
        record = AR.page_record(url, text, title="Posting", captured_at=at)
        await archive.record([await archive.entry_for_record(url, "page", record, archived_at=at)])
    rows = fetch("SELECT count(*) FROM link_archive WHERE account_id = %s", w.account)
    assert rows[0][0] == 2  # two contents, two copies
    async with tenant_txn(w.tenant) as s:
        hit = (await LA.lookup(s, w.account, [url]))[url]
    assert (await LA.archived_copy(store, hit)).text == "open text"
    assert hit.archived_at == T0 + timedelta(days=2)
    # an older capture of a copy never moves it back
    archive = _archive(w, store, T0)
    record = AR.page_record(url, "open text", title="Posting", captured_at=T0)
    await archive.record([await archive.entry_for_record(url, "page", record, archived_at=T0)])
    async with tenant_txn(w.tenant) as s:
        assert (await LA.lookup(s, w.account, [url]))[url].archived_at == T0 + timedelta(days=2)


async def test_only_archived_at_may_change_and_only_forward() -> None:
    from tests.conftest import APP_DSN

    w = make_world()
    store = MemoryPayloadStore()
    await _archive(w, store).put_text(PAGE, "page", "text")

    def update(sql: str, *params: object) -> None:
        with psycopg.connect(APP_DSN) as c, c.transaction():
            c.execute(
                "SELECT set_config('app.tenant_id', %s, true),"
                " set_config('app.scope', 'system', true)",
                (str(w.tenant),),
            )
            c.execute(sql, params)

    update("UPDATE link_archive SET archived_at = archived_at + interval '1 day'")
    with pytest.raises(errors.CheckViolation):
        update("UPDATE link_archive SET archived_at = archived_at - interval '30 days'")
    with pytest.raises(errors.InsufficientPrivilege):
        update("UPDATE link_archive SET title = 'renamed'")
    with pytest.raises(errors.InsufficientPrivilege):
        update("DELETE FROM link_archive")


# ---- F21: two users of one client on the archive routes --------------------------------------


async def test_another_user_of_the_same_client_cannot_reach_the_archive_of_a_private_account() -> (
    None
):
    from account_intelligence.api.auth import Identity, identity
    from tests.phase3.conftest import add_member, make_app

    w = make_world()  # the account belongs to test-owner
    store = MemoryPayloadStore()
    await _page(w, store, PAGE, "News", "A page.")

    def plane_as(user: str) -> Any:
        add_member(w.tenant, user, "admin")
        plane = make_app()
        plane.app.state.container.payloads = store
        now = datetime.now(UTC)

        async def ident() -> Identity:
            return Identity(user, w.tenant, "admin", now)

        plane.app.dependency_overrides[identity] = ident
        return plane

    base = f"/v1/accounts/{w.domain}/archive"
    mine = plane_as("test-owner")
    assert (await _get(mine, base, params={"url": PAGE})).status_code == 200
    theirs = plane_as("colleague-b")
    assert (await _get(theirs, base, params={"url": PAGE})).status_code == 404
    assert (await _get(theirs, base + "/list")).status_code == 404


async def test_the_archive_reads_have_a_per_user_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from identity import rate_limit

    assert rate_limit.user_limits()["archive_read"][0] >= 60  # the reviewed value
    monkeypatch.setattr(rate_limit, "user_limits", lambda: {"archive_read": (2, 0.001)})
    monkeypatch.setattr(rate_limit, "_user_memory", {})
    monkeypatch.setattr(rate_limit, "get_settings", lambda: type("S", (), {"redis_url": None})())
    w = make_world()
    store = MemoryPayloadStore()
    await _page(w, store, PAGE, "News", "A page.")
    plane = _plane_for(w, store)
    base = f"/v1/accounts/{w.domain}/archive"
    assert (await _get(plane, base + "/list")).status_code == 200
    assert (await _get(plane, base, params={"url": PAGE})).status_code == 200
    refused = await _get(plane, base + "/list")
    assert refused.status_code == 429 and int(refused.headers["Retry-After"]) >= 1


# ---- pipeline review 26: a person-name search is archived only under a valid consent ----------


async def test_a_person_name_search_result_is_not_archived_without_people_consent() -> None:
    from agents.people_names import PERSON_ROWS
    from agents.ports import RunContext, SourceItem
    from agents.production.payloads import StorePayloads

    w = make_world()  # the tenant has recorded no people data consent
    store = MemoryPayloadStore()
    payloads = StorePayloads(store, w.account, archive=_archive(w, store))
    ctx = RunContext(w.tenant, w.run, w.domain, "Synthetic Acme", w.domain, False, "sk-1")
    person_row = next(iter(PERSON_ROWS))
    await payloads.put(ctx, person_row, SourceItem(JANE, "Jane Doe spoke at a summit."), "public")
    await payloads.put(ctx, "some_other_row", SourceItem(NEUTRAL, "A company page."), "public")
    archived = [
        r[0] for r in fetch("SELECT url FROM link_archive WHERE account_id = %s", w.account)
    ]
    assert archived == [NEUTRAL]
