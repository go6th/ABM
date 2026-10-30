"""Source ID allocator (data layer v1, step 10): stable, gapless, concurrency-safe."""

from __future__ import annotations

import random
import threading
from uuid import UUID

import psycopg
import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from account_intelligence.db.enums import SourceKind
from account_intelligence.db.models import AccountSourceCounter, Source
from account_intelligence.db.source_ids import format_source_code, get_or_create_source
from tests.db.conftest import make_account, make_run, make_tenant, seed_complete_tracker, set_tenant


@pytest.fixture
def ctx(admin):  # type: ignore[no-untyped-def]
    tenant = make_tenant(admin)
    account = make_account(admin, tenant)
    return tenant, account, make_run(admin, tenant, account)


def _source(session: Session, account: UUID, run: UUID, url: str) -> Source:
    return get_or_create_source(
        session, account_id=account, run_id=run, url=url, kind=SourceKind.PRIMARY
    )


def test_format_source_code() -> None:
    assert format_source_code(1) == "S-0001"
    assert format_source_code(9999) == "S-9999"
    assert format_source_code(10000) == "S-10000"
    with pytest.raises(ValueError):
        format_source_code(0)


def test_same_url_twice_returns_the_same_source(db, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, run = ctx
    set_tenant(db, tenant)
    first = _source(db, account, run, "https://news.example.com/a")
    second = _source(db, account, run, "https://news.example.com/a")
    assert first.id == second.id
    assert first.source_code == "S-0001"


def test_url_variants_that_normalise_equal_share_a_code(db, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, run = ctx
    set_tenant(db, tenant)
    variants = [
        "https://News.Example.com/a/",
        "https://news.example.com:443/a?utm_source=mail",
        "https://news.example.com/a#top",
        "HTTPS://news.example.com/a?gclid=x",
    ]
    codes = {_source(db, account, run, url).source_code for url in variants}
    assert codes == {"S-0001"}


def test_codes_are_gapless_in_order_of_first_sight(db, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, run = ctx
    set_tenant(db, tenant)
    codes = [_source(db, account, run, f"https://example.com/{n}").source_code for n in range(12)]
    assert codes == [format_source_code(n) for n in range(1, 13)]
    counter = db.get(AccountSourceCounter, account)
    assert counter is not None and counter.next_value == 13


def test_codes_are_per_account(db, admin, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, run = ctx
    other = make_account(admin, tenant)
    other_run = make_run(admin, tenant, other)
    set_tenant(db, tenant)
    assert _source(db, account, run, "https://example.com/x").source_code == "S-0001"
    assert _source(db, other, other_run, "https://example.com/x").source_code == "S-0001"


def test_refresh_reuses_codes_and_moves_last_seen(db, admin, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, run = ctx
    admin.execute("UPDATE runs SET status = 'running', started_at = now() WHERE id = %s", (run,))
    seed_complete_tracker(admin, tenant, run)  # completion guard satisfied (migration 0010)
    admin.execute("UPDATE runs SET status = 'completed', finished_at = now() WHERE id = %s", (run,))
    refresh = make_run(admin, tenant, account, run_type="refresh", parent=run)
    set_tenant(db, tenant)
    urls = [f"https://example.com/report/{n}" for n in range(3)]
    before = {url: _source(db, account, run, url).source_code for url in urls}
    new = _source(db, account, refresh, "https://example.com/new-in-refresh")
    after = {url: _source(db, account, refresh, url) for url in urls}
    assert {url: s.source_code for url, s in after.items()} == before
    assert all(s.first_seen_run_id == run and s.last_seen_run_id == refresh for s in after.values())
    assert new.source_code == "S-0004"


def test_rollback_releases_the_code(sync_engine: Engine, admin, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, run = ctx
    with Session(sync_engine) as session, session.begin():
        set_tenant(session, tenant)
        _source(session, account, run, "https://example.com/kept")
    with Session(sync_engine) as session:
        session.begin()
        set_tenant(session, tenant)
        assert _source(session, account, run, "https://example.com/lost").source_code == "S-0002"
        session.rollback()
    with Session(sync_engine) as session, session.begin():
        set_tenant(session, tenant)
        assert _source(session, account, run, "https://example.com/next").source_code == "S-0002"


def test_invalid_url_is_rejected_before_touching_the_counter(db, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, run = ctx
    set_tenant(db, tenant)
    with pytest.raises(ValueError):
        _source(db, account, run, "not a url")
    assert db.get(AccountSourceCounter, account) is None


def test_counter_cannot_move_backwards(admin, sync_engine: Engine, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, run = ctx
    with Session(sync_engine) as session, session.begin():
        set_tenant(session, tenant)
        _source(session, account, run, "https://example.com/a")
    with pytest.raises(psycopg.errors.CheckViolation, match="cannot move backwards"):
        admin.execute(
            "UPDATE account_source_counters SET next_value = 1 WHERE account_id = %s", (account,)
        )


def test_concurrent_workers_never_share_a_code(sync_engine: Engine, ctx) -> None:
    """8 workers race over 20 overlapping URLs, one transaction per URL."""
    tenant, account, run = ctx
    pool = [f"https://example.com/doc/{n}" for n in range(20)]
    workers, start = 8, threading.Barrier(8)
    seen: list[tuple[str, str]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def work(seed: int) -> None:
        urls = random.Random(seed).sample(pool, k=len(pool))
        try:
            start.wait()
            for url in urls:
                with Session(sync_engine) as session, session.begin():
                    set_tenant(session, tenant)
                    code = _source(session, account, run, url).source_code
                with lock:
                    seen.append((url, code))
        except BaseException as error:  # surfaced to the main thread below
            errors.append(error)

    threads = [threading.Thread(target=work, args=(seed,)) for seed in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors

    code_by_url: dict[str, set[str]] = {}
    for url, code in seen:
        code_by_url.setdefault(url, set()).add(code)
    assert all(len(codes) == 1 for codes in code_by_url.values()), code_by_url
    codes = sorted(next(iter(codes)) for codes in code_by_url.values())
    assert codes == [format_source_code(n) for n in range(1, 21)]  # distinct and gapless

    with Session(sync_engine) as session, session.begin():
        set_tenant(session, tenant)
        stored = session.scalars(select(Source.source_code).where(Source.account_id == account))
        assert sorted(stored) == codes


# ---- titles from the link archive (workspace repair, 2026-10-31) --------------------------------
def _archived(session: Session, account: UUID, run: UUID, url: str, title: str, kind: str) -> None:
    from sqlalchemy import text

    from account_intelligence.urls import normalize_url, url_hash

    norm = normalize_url(url)
    session.execute(
        text(
            "INSERT INTO link_archive (account_id, run_id, url, url_normalized, url_hash, kind,"
            " payload_ref, title, archived_at) VALUES (:a, :r, :u, :n, :h, :k, :p, :t, now())"
        ),
        {"a": account, "r": run, "u": url, "n": norm, "h": url_hash(norm), "k": kind,
         "p": f"ref-{kind}-{title[:6]}", "t": title},
    )  # fmt: skip


def test_a_new_source_takes_the_title_the_page_was_archived_with(db, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, run = ctx
    set_tenant(db, tenant)
    url = "https://news.example.com/titled"
    _archived(db, account, run, url, "Acme names a new chief officer", "page")
    assert _source(db, account, run, url).title == "Acme names a new chief officer"


def test_a_source_stored_without_a_title_gets_it_when_seen_again(db, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, run = ctx
    set_tenant(db, tenant)
    url = "https://news.example.com/late"
    assert _source(db, account, run, url).title is None
    _archived(db, account, run, url, "Quarterly results released", "page")
    assert _source(db, account, run, url).title == "Quarterly results released"


def test_a_title_already_stored_is_kept_and_a_profile_copy_never_lends_one(db, ctx) -> None:  # type: ignore[no-untyped-def]
    tenant, account, run = ctx
    set_tenant(db, tenant)
    kept = get_or_create_source(
        db, account_id=account, run_id=run, url="https://news.example.com/kept",
        kind=SourceKind.PRIMARY, title="Own title",
    )  # fmt: skip
    _archived(db, account, run, "https://news.example.com/kept", "Other title", "page")
    assert (
        _source(db, account, run, "https://news.example.com/kept").title
        == kept.title
        == "Own title"
    )
    profile = "https://www.linkedin.com/in/some-person"
    _archived(db, account, run, profile, "Some Person - Head of Things", "profile")
    assert _source(db, account, run, profile).title is None
