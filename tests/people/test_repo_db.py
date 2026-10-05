"""People persistence on real Postgres (spec 6, 9.8): round trip, re-entry, raw-SQL refusals as
``app_rw``, append-only grants, tenant isolation, erasure and a resume on the real tables."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import errors

from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.hermetic import MemoryPayloadStore
from people.models import Classification, Profile
from people.pipeline import PipelineResult
from people.repo import PgPeopleRepo
from people.stage import run_people
from tests.fakes.people import Corpus, Crash, FakeRunInput
from tests.people.kit import Kit
from tests.people.test_resume import rows_hash
from tests.tracker.world import World, fetch, make_world

pytestmark = [pytest.mark.requires_postgres]


def _repo(w: World, payloads: MemoryPayloadStore | None = None) -> PgPeopleRepo:
    store = payloads or MemoryPayloadStore()
    return PgPeopleRepo(
        tenant_txn, w.tenant, w.account, store, lambda kind, name: f"t/{w.run}/{kind}/{name}"
    )


async def _run(
    w: World, repo: PgPeopleRepo, corpus: Corpus | None = None
) -> tuple[PipelineResult, Kit]:
    corpus = corpus or Corpus()
    kit = Kit(corpus, repo=repo)  # type: ignore[arg-type]
    result = await run_people(
        str(w.run),
        corpus.account,
        kit.pipeline,
        repo,
        kit.rec,
        kit.rec,
        kit.rec,
        FakeRunInput(corpus),
    )
    return result, kit


def _as_app_rw(tenant: UUID, sql: str, *params: object) -> list[Any]:
    from tests.conftest import APP_DSN

    with psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


@pytest.mark.asyncio
async def test_round_trip_and_reentry_makes_no_paid_call() -> None:
    w = make_world()
    repo = _repo(w)
    result, kit = await _run(w, repo)
    loaded = await repo.load_run(str(w.run))
    assert loaded is not None
    assert loaded.people == result.people
    assert loaded.profiles == result.profiles
    assert loaded.posts == result.posts
    assert loaded.counts == result.counts and loaded.claims == result.claims
    assert loaded.findings == result.findings and loaded.evidence == result.evidence
    paid = kit.ledger.paid
    again, kit2 = await _run(w, repo, kit.corpus)
    assert again.replayed and kit.corpus.ledger.paid == paid
    n = fetch("SELECT count(*) FROM people WHERE run_id = %s", w.run)[0][0]
    assert n == len(result.people)
    events = fetch(
        "SELECT kind, count(*) FROM person_events WHERE run_id = %s GROUP BY kind ORDER BY kind",
        w.run,
    )
    assert dict(events) == {"merge": 5, "scope": len(result.people) - 5}  # type: ignore[arg-type]
    record = fetch("SELECT record_json::text FROM people_pipeline_runs WHERE run_id = %s", w.run)[
        0
    ][0]
    for p in result.people:  # the run record carries no name or title
        for value in (p.name, p.title):
            if value:
                assert value not in str(record)


@pytest.mark.asyncio
async def test_resume_on_real_tables_after_a_crash_mid_profiles() -> None:
    baseline_w = make_world()
    baseline, base_kit = await _run(baseline_w, _repo(baseline_w))
    baseline_paid = base_kit.corpus.ledger.paid
    w = make_world()
    payloads = MemoryPayloadStore()
    repo = _repo(w, payloads)
    corpus = Corpus()
    real_save = repo.save_profiles
    calls = {"n": 0}

    async def crashing(run_id: str, profiles: Sequence[Profile]) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            await real_save(run_id, profiles[:2])  # half the batch reached the database
            raise Crash("killed mid-profile batch")
        await real_save(run_id, profiles)

    repo.save_profiles = crashing  # type: ignore[method-assign]
    with pytest.raises(Crash):
        await _run(w, repo, corpus)
    resumed, _ = await _run(w, _repo(w, payloads), corpus)
    assert rows_hash(resumed) == rows_hash(baseline)
    assert corpus.ledger.paid == baseline_paid  # every call before the crash replays for free
    pages = fetch("SELECT count(*) FROM person_search_pages WHERE run_id = %s", w.run)[0][0]
    assert (
        pages
        == fetch("SELECT count(*) FROM person_search_pages WHERE run_id = %s", baseline_w.run)[0][0]
    )


def _second_run(first: World) -> World:
    """Another run on the same account (as make_world writes runs, with the same tenant)."""
    from tests.tracker.world import admin_dsn

    run = uuid4()
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "INSERT INTO runs (id, tenant_id, account_id, run_type, status, config_version,"
            " credit_cap, started_at) VALUES (%s, %s, %s, 'full', 'running', 'cfg', 100, now())",
            (run, first.tenant, first.account),
        )
        c.execute(
            "INSERT INTO account_runs (id, tenant_id, account_id, status, graph_version,"
            " budget_json, reserved_credits, idempotency_key, budget_usd, tool_call_cap)"
            " VALUES (%s, %s, %s, 'RUNNING', 'test', '{}', 1, %s, 5, 60)",
            (run, first.tenant, first.account, "k-" + uuid4().hex),
        )
    return World(first.tenant, first.account, run, first.domain)


def _person_sql(w: World, **cols: object) -> tuple[str, list[object]]:
    base: dict[str, object] = {
        "tenant_id": w.tenant,
        "account_id": w.account,
        "run_id": w.run,
        "provider_person_id": "rec-" + uuid4().hex[:8],
        "source_ref": "raw",
        "person_key": "p" + uuid4().hex[:24],
    }
    base.update(cols)
    names = ", ".join(base)
    marks = ", ".join(["%s"] * len(base))
    return f"INSERT INTO people ({names}) VALUES ({marks}) RETURNING id", list(base.values())


def _insert(w: World, **cols: object) -> UUID:
    sql, params = _person_sql(w, **cols)
    return _as_app_rw(w.tenant, sql, *params)[0][0]  # type: ignore[no-any-return]


IN_SCOPE = {"scope_status": "in_scope", "priority": "A", "job_level": "director"}


def test_database_refuses_inconsistent_people_rows() -> None:
    w = make_world()
    with pytest.raises(errors.CheckViolation):
        _insert(w, **IN_SCOPE)  # in scope without a profile URL
    with pytest.raises(errors.CheckViolation):
        _insert(w, scope_status="excluded", excluded=False)  # excluded without the reason
    with pytest.raises(errors.CheckViolation):
        _insert(w, scope_status="review")  # review without a reason
    with pytest.raises(errors.CheckViolation):
        _insert(w, scope_status="out_of_scope", scope_reason="x", priority="A")
    with pytest.raises(errors.CheckViolation):
        _insert(w, job_level="intern")
    head = _insert(
        w,
        profile_url="https://www.linkedin.com/in/a",
        profile_url_canonical="https://www.linkedin.com/in/a",
        **IN_SCOPE,
    )
    child = _insert(w, scope_status="merged", merged_into=head)
    with pytest.raises(errors.CheckViolation):
        _insert(w, scope_status="merged", merged_into=child)  # merge into a merged row
    with pytest.raises(errors.ForeignKeyViolation):
        _insert(w, scope_status="merged", merged_into=uuid4())
    with pytest.raises(errors.CheckViolation):  # self-merge
        _as_app_rw(
            w.tenant,
            "UPDATE people SET merged_into = id, scope_status = 'merged', priority = NULL WHERE id"
            " = %s",
            head,
        )
    with pytest.raises(errors.CheckViolation):  # a row others point at cannot be merged
        other = _insert(
            w,
            profile_url="https://www.linkedin.com/in/b",
            profile_url_canonical="https://www.linkedin.com/in/b",
            **IN_SCOPE,
        )
        _as_app_rw(
            w.tenant,
            "UPDATE people SET merged_into = %s, scope_status = 'merged', priority = NULL WHERE id"
            " = %s",
            other,
            head,
        )
    with pytest.raises(errors.UniqueViolation):  # two canonical rows for one person in a run
        _insert(w, profile_url_canonical="https://www.linkedin.com/in/a", scope_status="unresolved")


def test_no_delete_and_append_only_tables() -> None:
    w = make_world()
    head = _insert(
        w,
        profile_url="https://www.linkedin.com/in/c",
        profile_url_canonical="https://www.linkedin.com/in/c",
        **IN_SCOPE,
    )
    with pytest.raises(errors.InsufficientPrivilege):
        _as_app_rw(w.tenant, "DELETE FROM people WHERE id = %s", head)
    _as_app_rw(
        w.tenant,
        "INSERT INTO person_events (tenant_id, account_id, run_id, person_key, kind, rule_id,"
        " actor)"
        " VALUES (%s, %s, %s, 'pk', 'scope', 'r', 'pipeline')",
        w.tenant,
        w.account,
        w.run,
    )
    for table in (
        "person_events",
        "person_search_pages",
        "person_post_pulls",
        "people_pipeline_runs",
        "person_sources",
        "person_profiles",
        "person_posts",
    ):
        with pytest.raises(errors.InsufficientPrivilege):
            _as_app_rw(w.tenant, f"DELETE FROM {table}")
    with pytest.raises(errors.InsufficientPrivilege):
        _as_app_rw(w.tenant, "UPDATE person_events SET rule_id = 'x'")
    _as_app_rw(
        w.tenant,
        "INSERT INTO person_profiles (tenant_id, account_id, run_id, profile_url_canonical,"
        " person_key,"
        " provider_code, raw_ref, call_ref, name, certifications_provided) VALUES"
        " (%s, %s, %s, 'https://www.linkedin.com/in/c', 'pk', 'p_people', 'raw', 'call',"
        " 'Synthetic', false)",
        w.tenant,
        w.account,
        w.run,
    )
    with pytest.raises(errors.InsufficientPrivilege):  # a granted column, but not an erasure
        _as_app_rw(w.tenant, "UPDATE person_profiles SET name = 'Other'")
    with pytest.raises(errors.InsufficientPrivilege):  # a column with no UPDATE grant
        _as_app_rw(w.tenant, "UPDATE person_profiles SET provider_code = 'x'")
    with pytest.raises(errors.CheckViolation):  # an "erasure" that leaves a personal field
        _as_app_rw(
            w.tenant,
            "UPDATE person_profiles SET name = NULL, join_date = NULL, own_description = NULL,"
            " erased_at = now(), erasure_reason = 'x', certifications = NULL, location = 'x'",
        )


def test_two_tenants_with_the_same_account_domain_never_cross() -> None:
    a = make_world()
    b = make_world()
    from tests.tracker.world import admin_dsn

    with psycopg.connect(admin_dsn(), autocommit=True) as c:  # same domain in both tenants
        c.execute("UPDATE accounts SET domain = %s WHERE id = %s", (a.domain, b.account))
    _insert(
        a,
        profile_url="https://www.linkedin.com/in/shared",
        profile_url_canonical="https://www.linkedin.com/in/shared",
        name="Synthetic A",
        **IN_SCOPE,
    )
    seen = _as_app_rw(
        b.tenant,
        "SELECT count(*) FROM people WHERE profile_url_canonical ="
        " 'https://www.linkedin.com/in/shared'",
    )
    assert seen[0][0] == 0
    with pytest.raises(errors.InsufficientPrivilege):  # WITH CHECK: no row into another tenant
        sql, params = _person_sql(a, scope_status="unresolved")
        params[0] = a.tenant
        _as_app_rw(b.tenant, sql, *params)
    with pytest.raises(errors.ForeignKeyViolation):  # cannot point at another tenant's row
        head = _as_app_rw(a.tenant, "SELECT id FROM people WHERE run_id = %s", a.run)[0][0]
        _insert(b, scope_status="merged", merged_into=head)


@pytest.mark.asyncio
async def test_erasure_keeps_identity_and_merge_chain() -> None:
    w = make_world()
    repo = _repo(w)
    result, _ = await _run(w, repo)
    jane = next(
        p
        for p in result.people
        if p.status == "in_scope" and (p.profile_url or "").endswith("/jane-doe")
    )
    before = fetch(
        "SELECT person_key, scope_status, merged_into FROM people WHERE run_id = %s ORDER BY"
        " person_key",
        w.run,
    )
    erased = await repo.erase(jane.profile_url, [], "synthetic erasure request")
    assert erased == 2  # the canonical row and the merged variant
    after = fetch(
        "SELECT person_key, scope_status, merged_into FROM people WHERE run_id = %s ORDER BY"
        " person_key",
        w.run,
    )
    assert after == before  # identity, statuses and the merge chain are kept
    rows = fetch(
        "SELECT name, title, erased_at IS NOT NULL FROM people WHERE account_id = %s AND"
        " profile_url_canonical = %s",
        w.account,
        jane.profile_url,
    )
    assert rows and all(r == (None, None, True) for r in rows)
    prof = fetch(
        "SELECT name, own_description, prior_employers, erased_at IS NOT NULL FROM person_profiles"
        " WHERE account_id = %s AND profile_url_canonical = %s",
        w.account,
        jane.profile_url,
    )
    assert prof == [(None, None, None, True)]
    posts = fetch(
        "SELECT excerpt FROM person_posts WHERE account_id = %s AND profile_url_canonical = %s",
        w.account,
        jane.profile_url,
    )
    assert posts and all(p == ("",) for p in posts)
    assert (
        fetch("SELECT count(*) FROM person_events WHERE kind = 'erase' AND run_id = %s", w.run)[0][
            0
        ]
        == 2
    )
    with pytest.raises(errors.CheckViolation):  # never refilled
        _as_app_rw(
            w.tenant,
            "UPDATE people SET name = 'Again' WHERE profile_url_canonical = %s",
            jane.profile_url,
        )
    with pytest.raises(errors.CheckViolation):  # never un-erased
        _as_app_rw(
            w.tenant,
            "UPDATE people SET erased_at = NULL, erasure_reason = NULL WHERE profile_url_canonical"
            " = %s",
            jane.profile_url,
        )
    with pytest.raises(errors.InsufficientPrivilege):
        _as_app_rw(
            w.tenant,
            "UPDATE person_profiles SET erased_at = NULL WHERE profile_url_canonical = %s",
            jane.profile_url,
        )
    later = await repo.stored_profiles([jane.profile_url or ""])
    assert later[jane.profile_url or ""].name is None  # an erased person is never pulled again
    assert await repo.erase(jane.profile_url, [], "again") == 0


@pytest.mark.asyncio
async def test_unmerge_is_recorded_and_reversible() -> None:
    w = make_world()
    repo = _repo(w)
    result, _ = await _run(w, repo)
    merged = next(p for p in result.people if p.status == "merged")
    await repo.unmerge(
        str(w.run),
        merged.person_key,
        Classification(status="review", reason="unmerged", rule_id="unmerge.manual"),
    )
    row = fetch(
        "SELECT scope_status, merged_into FROM people WHERE run_id = %s AND person_key = %s",
        w.run,
        merged.person_key,
    )
    assert row == [("review", None)]
    kinds = fetch(
        "SELECT kind, actor FROM person_events WHERE run_id = %s AND person_key = %s ORDER BY id",
        w.run,
        merged.person_key,
    )
    assert kinds == [("merge", "pipeline"), ("unmerge", "operator")]


@pytest.mark.asyncio
async def test_profiles_are_reused_across_runs_and_posts_resume() -> None:
    first = make_world()
    await _run(first, _repo(first))
    second = _second_run(first)
    repo = _repo(second)
    corpus = Corpus()
    result, kit = await _run(second, repo, corpus)
    assert kit.profiles.pulled == []  # every readable profile was pulled by the first run
    loaded = await repo.load_run(str(second.run))
    assert loaded is not None and loaded.profiles == result.profiles
    assert {p.profile_state for p in result.people if p.status == "in_scope"} == {
        "reused",
        "failed",
    }

    w3 = make_world()
    payloads = MemoryPayloadStore()
    crashing_repo = _repo(w3, payloads)
    real = crashing_repo.save_posts
    seen = {"n": 0}

    async def crash_on_second(run_id: str, url: str, posts: Sequence[Any]) -> None:
        seen["n"] += 1
        await real(run_id, url, posts)
        if seen["n"] == 2:
            raise Crash("killed after the second posts pull was stored")

    crashing_repo.save_posts = crash_on_second  # type: ignore[method-assign]
    corpus3 = Corpus()
    with pytest.raises(Crash):
        await _run(w3, crashing_repo, corpus3)
    stored = await _repo(w3, payloads).stored_posts(str(w3.run))
    assert len(stored) == 2 and any(stored.values())
    resumed, kit3 = await _run(w3, _repo(w3, payloads), corpus3)
    assert kit3.posts.pulled == [u for u in kit3.posts.pulled if u not in stored]
    assert resumed.counts is not None and resumed.counts.posts_pulled == 2
