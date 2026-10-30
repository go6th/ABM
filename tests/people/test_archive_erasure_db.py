"""The erasure of a person reaches the link archive, the profile matcher never re-lists an erased
person, and an archive failure never fails the research (security review 2026-10-29: F2, F21;
pipeline review 6 and 12). SYNTHETIC data only."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest
from psycopg import errors

from account_intelligence import archive as LA
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.hermetic import MemoryPayloadStore
from agents.production.people_links import ArchiveResolver
from tests.conftest import APP_DSN
from tests.people.test_repo_db import _repo, _run
from tests.tracker.world import World, fetch, make_world

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
T0 = datetime(2026, 10, 27, 9, tzinfo=UTC)
JANE_RESULT = "https://www.linkedin.com/in/jane-doe-search-variant"
UNRELATED = "https://news.synthetic-wire.example/other-person"


def _archive_rows(w: World) -> dict[str, tuple[str, str | None]]:
    rows = fetch("SELECT url, kind, title FROM link_archive WHERE account_id = %s", w.account)
    return {str(r[0]): (str(r[1]), None if r[2] is None else str(r[2])) for r in rows}


def _as_app_rw(tenant: object, sql: str, *params: object, scope: str = "system") -> list[Any]:
    with psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.scope', %s, true)",
            (str(tenant), scope),
        )
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


async def test_erasing_a_person_deletes_their_archive_rows_and_no_one_elses() -> None:
    w = make_world()
    store = MemoryPayloadStore()
    repo = _repo(w, store)
    result, _ = await _run(w, repo)
    jane = next(
        p
        for p in result.people
        if p.status == "in_scope" and (p.profile_url or "").endswith("/jane-doe")
    )
    other_listed = {
        (p.profile_url_raw or p.profile_url)
        for p in result.people
        if p.status == "in_scope"
        and p.profile_url
        and not p.internal_only
        and p.merged_into is None
        and p.person_key != jane.person_key
        and p.name != jane.name  # a namesake's profile copy is deleted with hers (by title)
    }
    # a Phase 1 search result titled with her name (another address), and an unrelated page
    archive = LA.LinkArchive(
        tenant_txn, w.tenant, w.account, w.run, store, SimpleNamespace(now=lambda: T0)
    )
    await archive.put_text(
        JANE_RESULT, "profile", "x", title=f"{jane.name} - CIO - Synthetic | LinkedIn"
    )
    await archive.put_text(UNRELATED, "page", "A page.", title="Other news")
    before = _archive_rows(w)
    assert any(jane.name and jane.name in (t or "") for _k, t in before.values())
    assert other_listed <= set(before) and UNRELATED in before and JANE_RESULT in before
    jane_urls = {u for u in before if (jane.profile_url or "").split("/in/")[-1] in u}
    assert jane_urls

    assert await repo.erase(jane.profile_url, [], "synthetic erasure request") == 2

    after = _archive_rows(w)
    assert not jane_urls & set(after) and JANE_RESULT not in after
    assert not any(jane.name and jane.name in (t or "") for _k, t in after.values())
    assert other_listed <= set(after) and UNRELATED in after  # nobody else's copy is touched
    # the profile matcher never sees her again (the index rows are gone)
    async with tenant_txn(w.tenant) as s:
        assert not {p.url for p in await LA.profile_links(s, w.account)} & jane_urls


async def test_the_erase_function_is_narrow() -> None:
    a, b = make_world(), make_world()
    store = MemoryPayloadStore()
    archive = LA.LinkArchive(
        tenant_txn, a.tenant, a.account, a.run, store, SimpleNamespace(now=lambda: T0)
    )
    await archive.put_text(UNRELATED, "page", "A page.", title="Other news")
    key = LA.normalized(UNRELATED)
    assert key is not None
    call = "SELECT erase_archive_urls(%s, %s::text[], NULL)"
    # nobody is erased in this account yet: the function refuses
    with pytest.raises(errors.InsufficientPrivilege):
        _as_app_rw(a.tenant, call, a.account, [key[1]])
    # another tenant's context cannot delete this tenant's rows, erased person or not
    fetch_ok = _as_app_rw(
        b.tenant, "SELECT count(*) FROM link_archive WHERE account_id = %s", a.account
    )
    assert fetch_ok == [(0,)]
    with pytest.raises(errors.InsufficientPrivilege):
        _as_app_rw(b.tenant, call, a.account, [key[1]])
    # no tenant context at all
    with psycopg.connect(APP_DSN) as c, pytest.raises(errors.InsufficientPrivilege):
        c.execute(call, (a.account, [key[1]]))
    # the function is not callable by the public role and is a definer owned by the eraser role
    owner = fetch(
        "SELECT r.rolname, p.prosecdef, r.rolbypassrls FROM pg_proc p"
        " JOIN pg_roles r ON r.oid = p.proowner WHERE p.proname = 'erase_archive_urls'"
    )
    assert owner == [("tenant_eraser", True, False)]
    assert _archive_rows(a) == {UNRELATED: ("page", "Other news")}


async def test_a_second_users_rows_are_erased_too() -> None:
    """The erase runs in the research worker's system scope: a copy stamped with another user
    (a colleague's resumed research) is deleted as well."""
    w = make_world()
    store = MemoryPayloadStore()
    repo = _repo(w, store)
    result, _ = await _run(w, repo)
    jane = next(
        p
        for p in result.people
        if p.status == "in_scope" and (p.profile_url or "").endswith("/jane-doe")
    )
    with psycopg.connect(__import__("tests.conftest", fromlist=["ADMIN_DSN"]).ADMIN_DSN) as c:
        # the table lets only archived_at move: lift the guard for this fixture update
        c.execute("ALTER TABLE link_archive DISABLE TRIGGER trg_link_archive_touch_only")
        c.execute(
            "UPDATE link_archive SET owner_user_id = 'colleague-b' WHERE account_id = %s",
            (w.account,),
        )
        c.execute("ALTER TABLE link_archive ENABLE TRIGGER trg_link_archive_touch_only")
    await repo.erase(jane.profile_url, [], "synthetic erasure request")
    assert not any((jane.profile_url or "").split("/in/")[-1] in u for u in _archive_rows(w))


async def test_the_resolver_never_relists_an_erased_person_and_wants_the_account_in_the_title() -> (
    None
):
    w = make_world()
    store = MemoryPayloadStore()
    repo = _repo(w, store)
    await _run(w, repo)
    archive = LA.LinkArchive(
        tenant_txn, w.tenant, w.account, w.run, store, SimpleNamespace(now=lambda: T0)
    )
    erased_url = "https://www.linkedin.com/in/erased-person"
    live_url = "https://www.linkedin.com/in/live-person"
    namesake = "https://www.linkedin.com/in/live-person-2"
    await archive.put_text(
        erased_url, "profile", "x", title="Erin Erased - CIO - Synthetic Acme | LinkedIn"
    )
    await archive.put_text(
        live_url, "profile", "x", title="Liv Live - CIO - Synthetic Acme | LinkedIn"
    )
    await archive.put_text(namesake, "profile", "x", title="Liv Live - CIO - Other Co | LinkedIn")
    # an earlier research listed Erin; she was erased (the tombstone keeps the canonical address)
    with psycopg.connect(__import__("tests.conftest", fromlist=["ADMIN_DSN"]).ADMIN_DSN) as c:
        c.execute(
            "INSERT INTO people (tenant_id, account_id, run_id, profile_url, profile_url_canonical,"
            " person_key, scope_status, erased_at, erasure_reason, source_record_id, origin,"
            " source_ref) VALUES (%s, %s, %s, %s, %s, 'pk-erin', 'unresolved', now(), 'synthetic',"
            " 'r-erin', 'title_search', 'raw')",
            (w.tenant, w.account, w.run, erased_url, erased_url),
        )
    deps = SimpleNamespace(
        txn=tenant_txn, tenant_id=w.tenant, account_id=w.account, domain=w.domain
    )
    resolver = ArchiveResolver(deps, "Synthetic Acme", ())
    assert (await resolver.resolve("Erin Erased", 0)).records == ()
    live = (await resolver.resolve("Liv Live", 0)).records
    assert [r.profile_url for r in live] == [live_url]  # the namesake of another company is out
    assert live[0].employer_domain == w.domain


async def test_an_archive_failure_never_fails_the_research() -> None:
    w = make_world()
    store = MemoryPayloadStore()
    real_put = store.put

    async def flaky(key: str, body: bytes) -> str:
        if "/archive/" in key:
            raise OSError("object store down")
        return await real_put(key, body)

    store.put = flaky  # type: ignore[method-assign]
    repo = _repo(w, store)
    result, _ = await _run(w, repo)  # no exception: the research stands
    assert result.people
    assert fetch("SELECT count(*) FROM people WHERE run_id = %s", w.run)[0][0] == len(result.people)
    assert _archive_rows(w) == {}

    # and a failing archive insert (the database side) leaves the people rows in place
    w2 = make_world()
    repo2 = _repo(w2, MemoryPayloadStore())

    async def broken(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("archive insert failed")

    from people import repo as people_repo

    original = people_repo.LA.record
    people_repo.LA.record = broken  # type: ignore[assignment]
    try:
        result2, _ = await _run(w2, repo2)
    finally:
        people_repo.LA.record = original  # type: ignore[assignment]
    assert fetch("SELECT count(*) FROM people WHERE run_id = %s", w2.run)[0][0] == len(
        result2.people
    )


async def test_the_0089_downgrade_maps_the_wider_levels_back_and_leaves_erasure_working() -> None:
    """Security review 2026-10-29, F8: the old level check came back NOT VALID, which still
    refuses every later UPDATE of a person with a level it does not know (the erasure tombstone
    included). The script now maps the levels back first and adds the check validated. Run inside
    a transaction that is rolled back, so the shared scratch schema is left as it was."""
    from pathlib import Path

    from tests.conftest import ADMIN_DSN
    from tests.people.test_repo_db import IN_SCOPE, _insert

    w = make_world()
    ids = {}
    for level in ("senior_director", "associate_director", "other", "vp"):
        url = f"https://www.linkedin.com/in/down-{level}"
        ids[level] = _insert(
            w,
            profile_url=url,
            profile_url_canonical=url,
            **{**IN_SCOPE, "job_level": level},
        )
    down = (
        Path(__file__).resolve().parents[2] / "migrations/0089_people_fields.down.sql"
    ).read_text()
    with psycopg.connect(ADMIN_DSN) as c:
        c.execute(down)
        levels = {
            level: c.execute("SELECT job_level FROM people WHERE id = %s", (pid,)).fetchone()[0]
            for level, pid in ids.items()
        }
        assert levels == {
            "senior_director": "director",
            "associate_director": "director",
            "other": None,
            "vp": "vp",
        }
        # the check is validated (not a NOT VALID one that blocks updates) and the tombstone works
        valid = c.execute(
            "SELECT convalidated FROM pg_constraint WHERE conname = 'ck_people_level'"
        ).fetchone()
        assert valid == (True,)
        for pid in ids.values():
            c.execute(
                "UPDATE people SET name = NULL, title = NULL, headline = NULL, location = NULL,"
                " erased_at = now(), erasure_reason = 'x' WHERE id = %s",
                (pid,),
            )
        c.rollback()


async def test_erasing_a_person_discards_the_account_s_materialised_web_tables() -> None:
    """F8: the web tables of every run of the account held the person; they are derived rows,
    so they go in the same transaction and the next read builds them again without the person."""
    w = make_world()
    repo = _repo(w, MemoryPayloadStore())
    result, _ = await _run(w, repo)
    jane = next(
        p
        for p in result.people
        if p.status == "in_scope" and (p.profile_url or "").endswith("/jane-doe")
    )
    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute("SET session_replication_role = replica")
        c.execute(
            "INSERT INTO view_materializations (tenant_id, run_id, account_id, counts, rows_hash,"
            " status, created_at) VALUES (%s, %s, %s, '{}'::jsonb, repeat('a', 64), 'ready',"
            " now())",
            (w.tenant, w.run, w.account),
        )
        c.execute(
            "INSERT INTO view_rows (tenant_id, run_id, account_id, table_kind, position, ref,"
            " row_id, cells, source_state) VALUES (%s, %s, %s, 'people', 1, 'P-0001', 'x',"
            " jsonb_build_object('name', %s::text), 'no_source')",
            (w.tenant, w.run, w.account, jane.name),
        )
    assert fetch("SELECT count(*) FROM view_rows WHERE run_id = %s", w.run)[0][0] == 1
    assert await repo.erase(jane.profile_url, [], "synthetic erasure request") >= 1
    assert fetch("SELECT count(*) FROM view_rows WHERE run_id = %s", w.run)[0][0] == 0
    assert fetch("SELECT count(*) FROM view_materializations WHERE run_id = %s", w.run)[0][0] == 0
