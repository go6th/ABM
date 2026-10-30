"""Pipeline review of 2026-10-29, fix wave A (parts that need PostgreSQL, synthetic data only).

* 2, 7, 19  the previous research is the newest by research date whose job pass is complete and
            whose matching inputs equal the current ones;
* 3         a carried posting's graded finding is recorded again for the new run;
* 4         a carried posting that slid out of the window is stored, not exported;
* 12        an object store failure while archiving does not fail ``save_run``;
* 13        a posting that closes and is open again shows as open in the archive;
* 20        ``create_run`` adds the incremental keys for every way a research starts;
* 25        a carried posting's observation advances with the research date.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
import pytest

from account_intelligence import archive as LA
from account_intelligence.db.session import tenant_txn
from account_intelligence.grading import repo as grading_repo
from account_intelligence.platform.hermetic import MemoryPayloadStore
from jobs.config import load_jobs_config
from jobs.incremental import (
    KEY_PREVIOUS_RUN,
    KEY_SINCE,
    Matching,
    current_matching,
    incremental_input,
    previous_delivered_run,
)
from jobs.repo import PgPostingRepo
from jobs.stage import run_jobs
from tests.fakes.jobs import Corpus, FakeRunInput, Recorder
from tests.grading.db_world import evidence, graded_confirmed, run_info
from tests.jobs.conftest import build, frozen_config
from tests.jobs.test_incremental import ACTIVE, NEW, OVERLAP, _delta_source
from tests.jobs.test_repo_db import _finish_run, _second_run
from tests.tracker.world import World, admin_dsn, fetch, make_world

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

CFG_NOW, CFG_BEFORE = "1" * 64, "2" * 64  # grading configuration hashes


class TextStore:
    """The payload store: the pipeline's stored texts, then whatever the archive adds. ``fail_put``
    makes every write fail (an object store outage)."""

    def __init__(self, rec: Recorder, *, fail_put: bool = False) -> None:
        self.rec, self.fail_put = rec, fail_put
        self.objects = MemoryPayloadStore()

    async def get(self, key: str) -> bytes:
        if key in self.rec.texts:
            return self.rec.texts[key].encode()
        return await self.objects.get(key)

    async def put(self, key: str, body: bytes) -> str:
        if self.fail_put:
            raise OSError("object store unavailable")
        return await self.objects.put(key, body)


def _deliver(w: World, run: UUID, *, tag: str, built: datetime | None = None) -> None:
    """A delivered Job Postings file of ``run`` (the row the previous-run lookup reads)."""
    digest = hashlib.sha256(tag.encode()).hexdigest()
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "INSERT INTO deliverables (tenant_id, account_id, run_id, set_hash, kind, version,"
            " supersedes, filename, sha256, size_bytes, object_key, manifest_json, content_hash,"
            " snapshot_hash, theme_hash, rules_hash, built_at) VALUES (%s, %s, %s, %s, 'jobs',"
            " (SELECT coalesce(max(version), 0) + 1 FROM deliverables WHERE tenant_id = %s"
            " AND account_id = %s AND kind = 'jobs'), (SELECT id FROM deliverables WHERE"
            " tenant_id = %s AND account_id = %s AND kind = 'jobs' ORDER BY version DESC LIMIT 1),"
            " 'jobs.xlsx', %s, 1, %s, '{}', %s, %s, %s, %s, %s)",
            (
                w.tenant,
                w.account,
                run,
                digest,
                w.tenant,
                w.account,
                w.tenant,
                w.account,
                digest,
                f"{w.tenant}/{w.account}/{run}/deliverables/{digest}.xlsx",
                digest,
                digest,
                digest,
                digest,
                built or datetime.now(UTC),
            ),
        )


def _matching(corpus: Corpus, **over: Any) -> Matching:
    cfg = frozen_config()
    return Matching(
        over.get("lanes_off", corpus.lanes_off),
        over.get("lexicon_hash", cfg.lexicon_hash),
        over.get("config_hash", cfg.config_hash),
    )


async def _research(
    w: World,
    run: UUID,
    corpus: Corpus,
    *,
    previous: Any = None,
    as_of: date | None = None,
    store: Recorder | None = None,
    delta: bool = False,
    **source_kw: Any,
) -> tuple[Any, PgPostingRepo, Recorder]:
    store = store or Recorder()
    pipeline, *_ = build(corpus, **({} if delta else source_kw))
    if delta:
        pipeline.source = _delta_source(corpus, [NEW, OVERLAP], ACTIVE, **source_kw)
    repo = PgPostingRepo(tenant_txn, w.tenant, w.account)
    result = await run_jobs(
        str(run),
        pipeline,
        repo,
        store,
        Recorder(),
        Recorder(),
        FakeRunInput(corpus.lanes_off, as_of or corpus.as_of, previous),
    )
    return result, repo, store


async def _base(w: World, corpus: Corpus, matching: Matching | None) -> tuple[UUID, date] | None:
    async with tenant_txn(w.tenant) as s:
        return await previous_delivered_run(s, w.account, matching)


# ---- findings 2, 7 and 19 ----------------------------------------------------------------------


async def test_the_base_is_the_newest_research_not_the_newest_rebuild() -> None:
    w, corpus = make_world(), Corpus()
    first, repo, store = await _research(w, w.run, corpus)
    previous = await repo.load_previous(str(w.run))
    run2 = _second_run(w)
    second, *_ = await _research(
        w,
        run2,
        corpus,
        previous=previous,
        as_of=corpus.as_of + timedelta(days=30),
        delta=True,
        store=store,
    )
    for run in (w.run, run2):
        _finish_run(run)
    _deliver(w, run2, tag="run2-files", built=datetime(2026, 10, 2, tzinfo=UTC))
    # the owner rebuilds the OLDER run's files afterwards (a new rules hash is a new file row)
    _deliver(w, w.run, tag="run1-rebuilt", built=datetime(2026, 11, 1, tzinfo=UTC))
    assert await _base(w, corpus, None) == (run2, second.as_of)  # not the rebuilt older run


async def test_a_research_that_did_not_pull_its_whole_feed_is_not_a_base() -> None:
    corpus = Corpus()
    cut = make_world()
    result, *_ = await _research(cut, cut.run, corpus, feed_complete=False, feed_exit="max_pages")
    assert result.exit_reasons["pull"] == "cap_reached" and not result.pull_complete
    _finish_run(cut.run)
    _deliver(cut, cut.run, tag="cut")
    assert await _base(cut, corpus, None) is None  # the missing months would never be pulled
    whole = make_world()
    await _research(whole, whole.run, corpus)
    _finish_run(whole.run)
    _deliver(whole, whole.run, tag="whole")
    assert await _base(whole, corpus, None) is not None
    # an invariant error in the stored pass also rules a run out
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "UPDATE job_pipeline_runs SET record_json = jsonb_set(record_json, '{invariant_error}',"
            " '\"counts do not add up\"') WHERE run_id = %s",
            (whole.run,),
        )
    assert await _base(whole, corpus, None) is None


async def test_a_research_with_other_matching_inputs_is_not_a_base() -> None:
    w, corpus = make_world(), Corpus()
    await _research(w, w.run, corpus)
    _finish_run(w.run)
    _deliver(w, w.run, tag="m")
    assert await _base(w, corpus, _matching(corpus)) == (w.run, corpus.as_of)
    # a lane switched on now was off then (or the reverse): the carried rows never named it
    assert await _base(w, corpus, _matching(corpus, lanes_off=frozenset())) is None
    assert await _base(w, corpus, _matching(corpus, lanes_off=frozenset({"veeva", "sap"}))) is None
    # a lexicon that grew since (new platform, new alias) needs every posting matched again
    assert await _base(w, corpus, _matching(corpus, lexicon_hash="0" * 64)) is None
    # a record from before the lexicon hash existed falls back to the config hash
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "UPDATE job_pipeline_runs SET record_json = record_json - 'lexicon_hash'"
            " - 'lanes_off' WHERE run_id = %s",
            (w.run,),
        )
    assert await _base(w, corpus, _matching(corpus)) == (w.run, corpus.as_of)
    assert await _base(w, corpus, _matching(corpus, config_hash="0" * 64)) is None


# ---- finding 20 --------------------------------------------------------------------------------


async def test_create_run_adds_the_incremental_keys_for_every_start() -> None:
    from tests.runs.test_lifecycle_db import _create, req, tenant

    t = tenant()
    cfg = load_jobs_config()
    first = await _create(t, req(lanes_off=["veeva"]))  # the account's first research
    async with tenant_txn(t) as s:
        assert (
            await s.scalar(
                __import__("sqlalchemy").text("SELECT run_input FROM account_runs WHERE id = :r"),
                {"r": first.run_id},
            )
            or {}
        ).get(KEY_PREVIOUS_RUN) is None  # nothing to carry yet
        account = await s.scalar(
            __import__("sqlalchemy").text("SELECT account_id FROM account_runs WHERE id = :r"),
            {"r": first.run_id},
        )
    # a finished, delivered research of the account with a complete pass on today's lexicon
    as_of = datetime.now(UTC).date() - timedelta(days=20)
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "INSERT INTO job_pipeline_runs (run_id, tenant_id, account_id, as_of, window_start,"
            " config_hash, record_json) VALUES (%s, %s, %s, %s, %s, %s, CAST(%s AS jsonb))",
            (
                first.run_id,
                t,
                account,
                as_of,
                as_of - timedelta(days=365),
                cfg.config_hash,
                '{"exit_reasons": {"pull": "converged"}, "pull_complete": true,'
                ' "lanes_off": ["veeva"], "lexicon_hash": "' + cfg.lexicon_hash + '"}',
            ),
        )
        c.execute("SET session_replication_role = replica")
        c.execute(
            "UPDATE account_runs SET status = 'COMPLETED', finished_at = now() WHERE id = %s",
            (first.run_id,),
        )
    _deliver(World(t, account, first.run_id, "acme.example"), first.run_id, tag="c")
    second = await _create(t, req(lanes_off=["veeva"]))
    async with tenant_txn(t) as s:
        stored = await s.scalar(
            __import__("sqlalchemy").text("SELECT run_input FROM account_runs WHERE id = :r"),
            {"r": second.run_id},
        )
    assert stored is not None
    want = incremental_input((first.run_id, as_of), datetime.now(UTC).date())
    assert want and {k: stored[k] for k in (KEY_PREVIOUS_RUN, KEY_SINCE)} == want
    # other lanes switched off: the matching inputs differ, so the whole window is pulled
    other = await _create(t, req(lanes_off=[]))
    # a file-only run pulls no job feed
    filed = await _create(t, req(input_mode="file", lanes_off=["veeva"]))
    # a Phase 2 run on a stored pull shares the pull's own keys
    build_run = await _create(
        t, req(mode="build", parent_run_id=str(second.run_id), lanes_off=["veeva"])
    )
    async with tenant_txn(t) as s:
        sql = __import__("sqlalchemy").text("SELECT run_input FROM account_runs WHERE id = :r")
        other_in = await s.scalar(sql, {"r": other.run_id})
        file_in = await s.scalar(sql, {"r": filed.run_id})
        build_in = await s.scalar(sql, {"r": build_run.run_id})
    assert KEY_PREVIOUS_RUN not in other_in and KEY_PREVIOUS_RUN not in file_in
    assert {k: build_in[k] for k in (KEY_PREVIOUS_RUN, KEY_SINCE)} == want
    assert current_matching(["veeva"]).lanes_off == frozenset({"veeva"})


# ---- finding 3 ---------------------------------------------------------------------------------


async def test_a_carried_postings_graded_finding_is_recorded_again_for_the_new_run() -> None:
    from agents.production.jobs_carry import carry_job_facts

    w = make_world()
    run2 = _second_run(w)
    url = "https://careers.acme.example/jobs/j1"
    one = graded_confirmed(
        attribute_key="jobs.salesforce.build",
        value="Salesforce",
        normalised_value="Salesforce",
        claim_hash="h-carry-1",
        config_hash=CFG_NOW,
        evidence=(evidence(url=url, supporting_sentence="You will build Salesforce triggers."),),
    )
    other_cfg = graded_confirmed(
        attribute_key="jobs.sap.build",
        value="SAP",
        normalised_value="SAP",
        claim_hash="h-carry-2",
        config_hash=CFG_BEFORE,
        evidence=(evidence(url="https://careers.acme.example/jobs/j2"),),
    )
    first = run_info(w.tenant, w.account, w.run)
    await grading_repo.record_graded_fact(tenant_txn, first, one, lane="salesforce")
    await grading_repo.record_graded_fact(tenant_txn, first, other_cfg, lane="sap")

    def claim(local_id: str, key: str, value: str, address: str) -> dict[str, Any]:
        return {
            "local_id": local_id,
            "attribute_key": key,
            "value": value,
            "evidence": [{"url": address, "supporting_sentence": "x"}],
        }

    claims = [
        claim("c1", "jobs.salesforce.build", "Salesforce", url + "#top"),  # same address
        claim("c2", "jobs.sap.build", "SAP", "https://careers.acme.example/jobs/j2"),
        claim("c3", "jobs.tableau.build", "Tableau", "https://careers.acme.example/jobs/j3"),
    ]
    carried, unmatched = await carry_job_facts(
        tenant_txn, run_info(w.tenant, w.account, run2), w.run, claims, CFG_NOW
    )
    # c2 was graded under another configuration, c3 was never graded: both are counted, not lost
    assert carried == {"c1"} and unmatched == {"c2", "c3"}
    rows = fetch(
        "SELECT f.attribute_key, f.value_text, f.product_grade, f.lane, e.url,"
        " e.supporting_sentence FROM facts f JOIN fact_evidence e ON e.fact_id = f.id"
        " WHERE f.run_id = %s",
        run2,
    )
    assert rows == [
        (
            "jobs.salesforce.build",
            "Salesforce",
            "confirmed",
            "salesforce",
            url,
            "You will build Salesforce triggers.",
        )
    ]
    # the previous run's facts are untouched, and a replay writes nothing twice
    await carry_job_facts(tenant_txn, run_info(w.tenant, w.account, run2), w.run, claims, CFG_NOW)
    assert fetch("SELECT count(*) FROM facts WHERE run_id = %s", run2) == [(1,)]
    assert fetch("SELECT count(*) FROM facts WHERE run_id = %s", w.run) == [(2,)]


# ---- findings 4 and 14 (the reader) ------------------------------------------------------------


async def test_a_carried_posting_past_the_window_is_stored_but_not_exported() -> None:
    from exports.readers.pg import PgContentReader

    w, corpus = make_world(), Corpus()
    first, repo, store = await _research(w, w.run, corpus)
    previous = await repo.load_previous(str(w.run))
    run2 = _second_run(w)
    later = corpus.as_of + timedelta(days=60)  # j11 (2025-10-01) is now past the window
    second, *_ = await _research(
        w, run2, corpus, previous=previous, as_of=later, delta=True, store=store
    )
    assert {p.posting_key: p.window_bucket for p in second.postings}["id:j11"] == "out_of_window"
    reader = PgContentReader(TextStore(store), batch=2)  # type: ignore[arg-type]

    async def listed(run: UUID) -> Any:
        async with tenant_txn(w.tenant) as s:
            return await reader._jobs(tenant_txn, w.tenant, w.account, str(run), s, w.domain)

    out1, out2 = await listed(w.run), await listed(run2)
    keys1 = {p.posting_key for p in out1["postings"]}
    keys2 = {p.posting_key for p in out2["postings"]}
    assert "id:j11" in keys1 and "id:j11" not in keys2  # kept stored, not listed any more
    assert fetch(
        "SELECT count(*) FROM job_postings WHERE run_id = %s AND posting_key = 'id:j11'", run2
    ) == [(1,)]
    # the rows, the counts printed beside them and the posting window agree
    counts = {k: v for k, _, v, _ in out2["jobs_counts"].lines}
    assert all(p.window_bucket != "out_of_window" for p in out2["postings"])
    assert len(out2["postings"]) <= counts["read"] <= counts["matched"] <= counts["in_window"]
    # the two readings of "classed" are counted apart and sum to the rows of the file
    assert counts["classed_by_reading"] + counts["classed_by_rule"] == len(out2["postings"])
    assert counts["classed_by_reading"] == sum(
        1 for p in out2["postings"] if p.classified_by == "extractor"
    )


async def test_a_board_only_postings_first_seen_is_the_day_it_was_first_captured() -> None:
    from jobs.models import RawPosting
    from tests.fakes.jobs import FakePostingSource

    w, corpus = make_world(), Corpus()
    url = "https://careers.acme.example/jobs/live-only"

    def raw(day: str) -> RawPosting:
        return RawPosting(
            title="Salesforce Developer",
            description="You will build Salesforce triggers as a developer. " * 8,
            url=url,
            provider_code="p",
            raw_ref="raw/live",
            retrieved_at=f"{day}T00:00:00+00:00",
            text_complete=True,
            live_board_present=True,
        )

    pipeline, *_ = build(corpus)
    pipeline.source = FakePostingSource({"Salesforce": [raw("2026-10-01")]}, [])
    store, repo = Recorder(), PgPostingRepo(tenant_txn, w.tenant, w.account)
    await run_jobs(
        str(w.run),
        pipeline,
        repo,
        store,
        Recorder(),
        Recorder(),
        FakeRunInput(corpus.lanes_off, corpus.as_of),
    )
    previous = await repo.load_previous(str(w.run))
    run2 = _second_run(w)
    pipeline, *_ = build(corpus)
    pipeline.source = FakePostingSource({"Salesforce": [raw("2026-12-01")]}, [], active=[])
    await run_jobs(
        str(run2),
        pipeline,
        repo,
        store,
        Recorder(),
        Recorder(),
        FakeRunInput(corpus.lanes_off, date(2026, 12, 1), previous),
    )
    from exports.readers.pg import PgContentReader

    reader = PgContentReader(TextStore(store), batch=2)  # type: ignore[arg-type]
    async with tenant_txn(w.tenant) as s:
        out = await reader._jobs(tenant_txn, w.tenant, w.account, str(run2), s, w.domain)
    posting = next(p for p in out["postings"] if p.url == url)
    assert posting.first_seen == date(2026, 10, 1)  # not the date of the file being built


# ---- findings 12, 13 and 25 --------------------------------------------------------------------


async def test_an_object_store_failure_does_not_fail_the_save() -> None:
    w, corpus = make_world(), Corpus()
    pipeline, *_ = build(corpus)
    rec = Recorder()
    repo = PgPostingRepo(tenant_txn, w.tenant, w.account, TextStore(rec, fail_put=True))  # type: ignore[arg-type]
    result = await run_jobs(
        str(w.run), pipeline, repo, rec, rec, rec, FakeRunInput(corpus.lanes_off, corpus.as_of)
    )
    assert await repo.load_run(str(w.run)) is not None
    rows = fetch(
        "SELECT count(*), count(*) FILTER (WHERE format = 'text') FROM link_archive"
        " WHERE run_id = %s",
        w.run,
    )
    with_text = sum(1 for p in result.postings if p.text_ref)
    assert rows == [(with_text, with_text)]  # every posting fell back to its stored text


async def test_a_posting_that_closes_and_opens_again_shows_as_open_in_the_archive() -> None:
    w, corpus = make_world(), Corpus()
    result, _, rec = await _research(w, w.run, corpus)
    store = TextStore(rec)
    repo = PgPostingRepo(tenant_txn, w.tenant, w.account, store)  # type: ignore[arg-type]

    async def archive(posting: Any, when: date) -> None:
        moved = result.__class__(**{**result.__dict__, "postings": (posting,)})
        entries = await repo._archive_entries(moved)
        async with tenant_txn(w.tenant) as s:
            await LA.record(s, w.tenant, w.account, w.run, entries)

    target = next(p for p in result.postings if p.text_ref and p.status == "open")
    carried = target.model_copy(update={"new_since_previous": False})
    day = lambda n: result.as_of + timedelta(days=n)  # noqa: E731
    await archive(carried.model_copy(update={"retrieved_at": f"{day(0)}T00:00:00+00:00"}), day(0))
    closed = carried.model_copy(
        update={"status": "closed", "status_since": day(10), "retrieved_at": f"{day(10)}T00:00:00Z"}
    )
    await archive(closed, day(10))
    reopened = carried.model_copy(
        update={"status": "open", "status_since": day(20), "retrieved_at": f"{day(20)}T00:00:00Z"}
    )
    await archive(reopened, day(20))
    async with tenant_txn(w.tenant) as s:
        newest = (await LA.lookup(s, w.account, [target.url]))[target.url]
    copy = await LA.archived_copy(store, newest)  # type: ignore[arg-type]
    assert copy.record is not None and copy.record["status_at_capture"] == "open"
    assert newest.archived_at.date() == day(20)
    # an unchanged status writes nothing new on the next research
    before = fetch("SELECT count(*) FROM link_archive WHERE url = %s", target.url)
    await archive(reopened, day(20))
    assert fetch("SELECT count(*) FROM link_archive WHERE url = %s", target.url) == before


async def test_a_carried_postings_observation_advances_with_the_research_date() -> None:
    w, corpus = make_world(), Corpus()
    first, repo, store = await _research(w, w.run, corpus)
    previous = await repo.load_previous(str(w.run))
    run2 = _second_run(w)
    later = corpus.as_of + timedelta(days=60)
    await _research(w, run2, corpus, previous=previous, as_of=later, delta=True, store=store)
    seen = await repo.first_and_last_seen("id:j18")  # carried, not in the delta
    assert seen is not None
    assert seen[0].date() == corpus.as_of and seen[1].date() == later  # type: ignore[attr-defined]
