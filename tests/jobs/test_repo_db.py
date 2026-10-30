"""Job pipeline persistence on real Postgres as ``app_rw`` (spec 9.9) and isolation."""

from __future__ import annotations

from uuid import UUID

import psycopg
import pytest
from psycopg import errors

from account_intelligence.db.session import tenant_txn
from jobs.repo import PgPostingRepo
from jobs.stage import run_jobs
from tests.fakes.jobs import Corpus, FakeRunInput, Recorder
from tests.jobs.conftest import build
from tests.tracker.world import World, admin_dsn, fetch, make_world

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


async def _saved(w: World, **kw: object) -> tuple[PgPostingRepo, object]:
    corpus = Corpus()
    pipeline, source, *_ = build(corpus, **kw)  # type: ignore[arg-type]
    rec = Recorder()
    repo = PgPostingRepo(tenant_txn, w.tenant, w.account)
    result = await run_jobs(
        str(w.run), pipeline, repo, rec, rec, rec, FakeRunInput(corpus.lanes_off, corpus.as_of)
    )
    return repo, (result, source)


def _as_app_rw(tenant: UUID, sql: str, *params: object) -> None:
    from tests.conftest import APP_DSN

    with psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.scope', 'system', true)",
            (str(tenant),),
        )
        c.execute(sql, params)


async def test_save_and_load_round_trip_and_reentry_makes_no_call() -> None:
    w = make_world()
    repo, (result, source) = await _saved(w)
    loaded = await repo.load_run(str(w.run))
    assert loaded is not None and loaded.postings == result.postings  # type: ignore[attr-defined]
    assert loaded.counts == result.counts  # type: ignore[attr-defined]
    calls = len(source.calls)  # type: ignore[attr-defined]
    corpus = Corpus()
    pipeline, again_source, *_ = build(corpus)
    rec = Recorder()
    replay = await run_jobs(
        str(w.run), pipeline, repo, rec, rec, rec, FakeRunInput(corpus.lanes_off, corpus.as_of)
    )
    assert replay.replayed and again_source.calls == [] and calls > 0
    n = fetch("SELECT count(*) FROM job_postings WHERE run_id = %s", w.run)[0][0]
    assert n == len(result.postings)  # type: ignore[attr-defined]
    s = fetch("SELECT count(*) FROM job_posting_platform_sentences WHERE run_id = %s", w.run)
    assert s[0][0] == sum(len(p.sentences) for p in result.postings)  # type: ignore[attr-defined]
    # every posting whose text was stored is archived under its link (format text: the stored
    # text itself is the copy)
    archived = fetch(
        "SELECT url, payload_ref, format, kind FROM link_archive WHERE run_id = %s ORDER BY url",
        w.run,
    )
    with_text = {(p.url, p.text_ref) for p in result.postings if p.text_ref}  # type: ignore[attr-defined]
    assert {(row[0], row[1]) for row in archived} == with_text and with_text
    assert {(row[2], row[3]) for row in archived} == {("text", "posting")}
    seen = await repo.first_and_last_seen("id:j1")
    assert seen is not None


async def test_heuristic_classifications_are_stored_and_read_back() -> None:
    """Migration 0088 widens classified_by: the heuristic's classes survive a save and a load."""
    w = make_world()
    repo, (result, _) = await _saved(w)
    by = fetch(
        "SELECT classified_by, count(*) FROM job_posting_classifications WHERE run_id = %s"
        " GROUP BY classified_by",
        w.run,
    )
    stored = {row[0]: row[1] for row in by}
    assert stored.get("heuristic", 0) >= 3 and stored.get("extractor", 0) >= 10
    loaded = await repo.load_run(str(w.run))
    assert loaded is not None
    kinds = {
        p.classification.classified_by
        for p in loaded.postings
        if p.classification  # type: ignore[attr-defined]
    }
    assert {"heuristic", "extractor"} <= kinds
    assert not stored.get("rule")


async def test_db_refuses_bad_rows_and_any_update_or_delete() -> None:
    w = make_world()
    await _saved(w)
    insert = (
        "INSERT INTO job_postings (account_id, run_id, posting_key, row_json, status,"
        " window_bucket, url, posting_class) VALUES (%s, %s, %s, '{}', %s, 'in_window', %s, %s)"
    )
    for status, url, klass in (
        ("open", None, "build"),  # no URL
        ("open", "https://x.example/1", "manager"),  # class outside the vocabulary
        ("pending", "https://x.example/2", "build"),  # status outside the vocabulary
    ):
        with pytest.raises(errors.CheckViolation):
            _as_app_rw(
                w.tenant, insert, w.account, w.run, f"k-{status}-{klass}", status, url, klass
            )
    for sql in (
        "UPDATE job_postings SET status = 'closed' WHERE run_id = %s",
        "DELETE FROM job_postings WHERE run_id = %s",
        "UPDATE job_posting_classifications SET posting_class = 'build' WHERE run_id = %s",
        "DELETE FROM job_posting_observations WHERE run_id = %s",
        "DELETE FROM job_pipeline_runs WHERE run_id = %s",
        "UPDATE job_pipeline_passes SET gap_exit = 'converged' WHERE run_id = %s",
        "DELETE FROM job_pipeline_passes WHERE run_id = %s",
    ):
        with pytest.raises(errors.InsufficientPrivilege):
            _as_app_rw(w.tenant, sql, w.run)


async def test_two_tenants_with_the_same_domain_never_cross() -> None:
    a, b = make_world(), make_world()
    await _saved(a)
    repo_b = PgPostingRepo(tenant_txn, b.tenant, b.account)
    assert await repo_b.load_run(str(a.run)) is None  # RLS: b cannot see a's record
    for table in (
        "job_postings",
        "job_posting_platform_sentences",
        "job_posting_classifications",
        "job_posting_observations",
        "job_pipeline_runs",
        "job_pipeline_passes",
    ):
        async with tenant_txn(b.tenant) as s:
            from sqlalchemy import text

            rows = (
                await s.execute(
                    text(f"SELECT count(*) FROM {table} WHERE run_id = :r"), {"r": a.run}
                )
            ).scalar()
        assert rows == 0, table
    with pytest.raises(errors.InsufficientPrivilege):  # WITH CHECK: cannot write as another tenant
        _as_app_rw(
            b.tenant,
            "INSERT INTO job_pipeline_runs (run_id, tenant_id, account_id, as_of, window_start,"
            " config_hash, record_json) VALUES (gen_random_uuid(), %s, %s, now(), now(), 'x',"
            " '{}')",
            a.tenant,
            a.account,
        )


async def test_gap_pass_appends_a_reclassification_and_resumes_from_the_last_pass() -> None:
    from jobs.errors import JobsError
    from tests.jobs.test_gap_and_resume import J5, _extractor, _flaky_once

    w = make_world()
    first = _extractor(Corpus(), **{"id:j5": _flaky_once(JobsError, J5)})
    repo, (result, source) = await _saved(w, extractor=first)
    assert result.pass_no == 1  # type: ignore[attr-defined]
    passes = fetch("SELECT pass_no, gap_exit FROM job_pipeline_passes WHERE run_id = %s", w.run)
    assert passes == [(1, "converged")]
    history = fetch(
        "SELECT pass_no, posting_class, reason FROM job_posting_classifications"
        " WHERE run_id = %s AND posting_key = 'id:j5' ORDER BY pass_no",
        w.run,
    )
    assert history == [(0, "unclassified", "error"), (1, "build", "extractor_verified")]
    # only the re-processed posting got new side rows
    assert fetch(
        "SELECT count(*) FROM job_posting_classifications WHERE run_id = %s AND pass_no = 1", w.run
    ) == [(1,)]
    loaded = await repo.load_run(str(w.run))
    assert loaded is not None and loaded.pass_no == 1  # type: ignore[attr-defined]
    assert loaded.counts == result.counts  # type: ignore[attr-defined]
    corpus = Corpus()
    pipeline, again, *_ = build(corpus)
    rec = Recorder()
    replay = await run_jobs(
        str(w.run), pipeline, repo, rec, rec, rec, FakeRunInput(corpus.lanes_off, corpus.as_of)
    )
    assert replay.replayed and again.calls == [] and replay.calls_made == 0
    assert fetch("SELECT count(*) FROM job_pipeline_passes WHERE run_id = %s", w.run) == [(1,)]


# ---- incremental re-research (jobs.incremental) ------------------------------------------------


def _second_run(w: World) -> UUID:
    """A second run of the same account (both run tables), as a later research creates one."""
    from uuid import uuid4

    run = uuid4()
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "INSERT INTO runs (id, tenant_id, account_id, run_type, status, config_version,"
            " credit_cap, started_at) VALUES (%s, %s, %s, 'full', 'running', 'cfg', 100, now())",
            (run, w.tenant, w.account),
        )
        c.execute(
            "INSERT INTO account_runs (id, tenant_id, account_id, status, graph_version,"
            " budget_json, reserved_credits, idempotency_key, budget_usd, tool_call_cap)"
            " VALUES (%s, %s, %s, 'RUNNING', 'test', '{}', 1, %s, 5, 60)",
            (run, w.tenant, w.account, "k-" + uuid4().hex),
        )
    return run


def _finish_run(run: UUID, status: str = "COMPLETED") -> None:
    """End a run in both run tables (a delivered file belongs to a run that ended)."""
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        # the completion guard wants a seeded tracker, which is not what these tests are about
        c.execute("SET session_replication_role = replica")
        c.execute(
            "UPDATE account_runs SET status = %s, finished_at = now() WHERE id = %s", (status, run)
        )


def _delivered(w: World, run: UUID) -> None:
    """A delivered Job Postings file for ``run`` (the row ``delivered_run`` reads)."""
    digest = "0" * 64
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "INSERT INTO deliverables (tenant_id, account_id, run_id, set_hash, kind, version,"
            " filename, sha256, size_bytes, object_key, manifest_json, content_hash,"
            " snapshot_hash, theme_hash, rules_hash, built_at) VALUES (%s, %s, %s, %s, 'jobs',"
            " (SELECT coalesce(max(version), 0) + 1 FROM deliverables WHERE tenant_id = %s"
            " AND account_id = %s AND kind = 'jobs'), 'jobs.xlsx', %s, 1, %s, '{}', %s, %s, %s,"
            " %s, now())",
            (
                w.tenant,
                w.account,
                run,
                digest,
                w.tenant,
                w.account,
                digest,
                f"{w.tenant}/{w.account}/{run}/deliverables/{digest}.xlsx",
                digest,
                digest,
                digest,
                digest,
            ),
        )


async def test_incremental_fields_round_trip_and_the_previous_run_is_this_accounts_only() -> None:
    from datetime import date, timedelta

    from jobs.incremental import previous_delivered_run
    from tests.jobs.test_incremental import ACTIVE, NEW, OVERLAP, _delta_source

    w = make_world()
    corpus = Corpus()
    pipeline, *_ = build(corpus)
    store = Recorder()  # the payload store: run 2 reads the carried texts back from it
    repo = PgPostingRepo(tenant_txn, w.tenant, w.account)
    first = await run_jobs(
        str(w.run),
        pipeline,
        repo,
        store,
        store,
        store,
        FakeRunInput(corpus.lanes_off, corpus.as_of),
    )
    assert fetch(
        "SELECT last_seen, seniority, new_since_previous FROM job_postings WHERE run_id = %s"
        " AND posting_key = 'id:j18'",
        w.run,
    ) == [(date(2026, 9, 15), "mid_senior", None)]
    assert fetch(
        "SELECT previous_run_id, carried FROM job_pipeline_runs WHERE run_id = %s", w.run
    ) == [(None, 0)]
    # the previous run resolves for this account only (RLS keeps other tenants out, the account
    # check keeps another account of the same tenant out)
    previous = await repo.load_previous(str(w.run))
    assert previous is not None and previous.postings == first.postings
    assert previous.as_of == first.as_of
    other_account = make_world(tenant=w.tenant)
    assert (
        await PgPostingRepo(tenant_txn, w.tenant, other_account.account).load_previous(str(w.run))
        is None
    )
    other_tenant = make_world()
    assert (
        await PgPostingRepo(tenant_txn, other_tenant.tenant, other_tenant.account).load_previous(
            str(w.run)
        )
        is None
    )
    # the delivered-run lookup needs a delivered file of a run with a job pipeline record
    async with tenant_txn(w.tenant) as s:
        assert await previous_delivered_run(s, w.account) is None
    _delivered(w, w.run)
    # a run that has not ended is not a base (review 2026-10-29, finding 7)
    async with tenant_txn(w.tenant) as s:
        assert await previous_delivered_run(s, w.account) is None
    _finish_run(w.run)
    async with tenant_txn(w.tenant) as s:
        assert await previous_delivered_run(s, w.account) == (w.run, first.as_of)  # type: ignore[attr-defined]
        assert await previous_delivered_run(s, other_account.account) is None
    async with tenant_txn(other_tenant.tenant) as s:
        assert await previous_delivered_run(s, w.account) is None
    # the second research of the account: carried rows are stored again under the new run with
    # the incremental columns filled; the record names the previous run
    pipeline, *_ = build(corpus)
    pipeline.source = _delta_source(corpus, [NEW, OVERLAP], ACTIVE)
    run2 = _second_run(w)
    rec = Recorder()
    second = await run_jobs(
        str(run2),
        pipeline,
        repo,
        store,
        rec,
        rec,
        FakeRunInput(corpus.lanes_off, corpus.as_of + timedelta(days=60), previous),
    )
    assert len(second.claims) == len(first.claims) + 1 == len(rec.claims)
    assert second.carried == len(first.postings) and second.previous_run_id == str(w.run)  # type: ignore[attr-defined]
    assert fetch(
        "SELECT previous_run_id, carried FROM job_pipeline_runs WHERE run_id = %s", run2
    ) == [(w.run, len(first.postings))]  # type: ignore[attr-defined]
    assert fetch(
        "SELECT posting_key, new_since_previous, status, last_seen, seniority FROM job_postings"
        " WHERE run_id = %s AND posting_key IN ('id:j18', 'id:j30', 'id:j2') ORDER BY posting_key",
        run2,
    ) == [
        ("id:j18", False, "open", date(2026, 9, 15), "mid_senior"),
        ("id:j2", False, "closed", None, None),
        ("id:j30", True, "open", date(2026, 11, 28), "mid_senior"),
    ]
    loaded = await repo.load_run(str(run2))
    assert loaded is not None and loaded.postings == second.postings  # type: ignore[attr-defined]
    assert loaded.previous_run_id == str(w.run) and loaded.carried == second.carried  # type: ignore[attr-defined]
    # the first run's rows are untouched (insert-only): still 21 rows, no flag
    assert fetch(
        "SELECT count(*) FROM job_postings WHERE run_id = %s AND new_since_previous IS NULL", w.run
    ) == [(len(first.postings),)]  # type: ignore[attr-defined]
