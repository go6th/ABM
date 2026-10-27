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
from tests.tracker.world import World, fetch, make_world

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
