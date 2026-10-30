"""Leakage review 2026-10-30, fix wave D2 (F1, F2, F7, F5, F6 database side): on real Postgres.

* F1: an exclusion added AFTER a run applies to every surface that serves the run afterwards: the
  rebuilt files and the workspace zip (people list, sources, ``build/urls.json``), the web tables
  (the cache is keyed by the exclusion set), the delivered-file listing (a stale file is held
  back until it is rebuilt), the live sheets and Ask.
* F2: facts, people and postings recorded again under a second run (a resume or an incremental
  re-research) are one row in every account-wide reader.
* F7: the change note of a rebuild compares the run with the previous run, not with itself.

GUARANTEE(test_an_exclusion_added_after_a_run_reaches_every_surface)
GUARANTEE(test_the_same_fact_recorded_under_two_runs_appears_once_everywhere)
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
import pytest

from account_intelligence.db.session import tenant_txn
from account_intelligence.grading.config import load_grading_config
from account_intelligence.replica import answers
from account_intelligence.replica import sheets as SH
from account_intelligence.replica import workspace as W
from exports.config import load_export_config
from tenancy import settings as tenant_settings
from tests.exports.test_db import _source, builder, seed_world
from tests.tracker.world import World, admin_dsn, fetch, make_world
from views import materialize

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
CFG, GRADING = load_export_config(), load_grading_config()
USER = "test-owner"


@contextlib.contextmanager
def _as_user(user: str) -> Iterator[None]:
    """A request of one user: user scope (the tests default to the system scope of a worker)."""
    from account_intelligence.db.session import _SYSTEM, acting_as, set_user_scope

    token = set_user_scope()
    try:
        with acting_as(user):
            yield
    finally:
        _SYSTEM.reset(token)


def _everything(data: bytes) -> str:
    """Every text of a package: members of zips (and of the zips inside them), decoded."""
    out: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for name in z.namelist():
                out.append(name)
                out.append(_everything(z.read(name)))
    except zipfile.BadZipFile:
        out.append(data.decode("utf-8", "ignore"))
    return "\n".join(out)


async def _exclude(
    w: World,
    *,
    people: tuple[str, ...] = (),
    companies: tuple[str, ...] = (),
    at: datetime | None = None,
) -> None:
    """Save a new settings version of the tenant with these exclusions (after the run)."""
    async with tenant_txn(w.tenant) as s:
        cur = await tenant_settings.current(s)
        data = cur.settings.model_dump(mode="json")
        data["exclusions"] = {"people": list(people), "companies": list(companies)}
        await tenant_settings.save(
            s,
            tenant_settings.parse(data),
            actor="test",
            expected_version=cur.version,
            now=at or datetime.now(UTC),
        )


async def _a_listed_person(b: Any, w: World) -> tuple[str, str]:
    """(name, profile url) of a person the run's People list prints."""
    content = await b.reader.read(str(w.tenant), str(w.run))
    assert content.people, "the synthetic corpus must list someone"
    # a person whose name and address no other listed person shares (the corpus has two Jane Does)
    for p in content.people:
        others = [q for q in content.people if q.id != p.id]
        slug = p.profile_url.rstrip("/").rsplit("/", 1)[-1]
        if not any(p.name == q.name or slug in q.profile_url for q in others):
            return p.name, p.profile_url
    raise AssertionError("no listed person with a name of their own")


def _finish(w: World) -> None:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute("SET session_replication_role = replica")  # the fixture run has no tracker rows
        c.execute(
            "UPDATE account_runs SET status = 'COMPLETED', finished_at = now() WHERE id = %s",
            (w.run,),
        )


async def test_an_exclusion_added_after_a_run_reaches_every_surface() -> None:
    w, payloads = await seed_world()
    _finish(w)
    b = builder(w, payloads)
    name, profile = await _a_listed_person(b, w)
    slug = profile.rstrip("/").rsplit("/", 1)[-1]

    # before: the person is in the delivered People list, the zip, the web table, the live
    # sheet and Ask's rows
    first = await b.build_all(str(w.tenant), str(w.run))
    assert first.status == "complete", first.errors
    people_file = next(d for d in first.deliverables if d.kind == "people")
    zipped = next(d for d in first.deliverables if d.kind == "workspace")
    assert name in _everything(payloads.objects[people_file.object_key])
    assert slug in _everything(payloads.objects[zipped.object_key])
    mat = await materialize.materialize(
        tenant_txn, b.reader, CFG, GRADING, w.tenant, w.run, datetime.now(UTC)
    )
    assert mat.counts["people"] > 0 and mat.exclusion_hash == "none"
    async with tenant_txn(w.tenant) as s:
        live = await SH.read(
            s, SH.parse("people", None, None, None), w.account, USER, datetime.now(UTC)
        )
        assert any(name in r["cells"]["name"] for r in live["rows"])
        assert any(name in r.text for r in (await answers.load_workspace(s, USER)).people)
        assert await W.stale_kinds(s, w.account) == []
        assert {f["kind"] for f in await W.deliverables(s, w.account)} == {
            "summary",
            "people",
            "jobs",
            "tech",
            "evidence",
        }

    # the admin excludes the person AFTER the run (the run pinned no exclusions at all)
    await _exclude(w, people=(profile,))

    async with tenant_txn(w.tenant) as s:
        # the stored files were built under another list: held back, not served, until rebuilt
        assert sorted(await W.stale_kinds(s, w.account)) == sorted(
            ["summary", "people", "jobs", "tech", "evidence"]
        )
        assert await W.deliverables(s, w.account) == []
        assert not await SH.is_materialised(s, w.run)  # the web tables are stale too
        # the live sheet and Ask no longer show the person
        live = await SH.read(
            s, SH.parse("people", None, None, None), w.account, USER, datetime.now(UTC)
        )
        assert not any(name in r["cells"]["name"] for r in live["rows"])
        assert not any(name in r.text for r in (await answers.load_workspace(s, USER)).people)

    # the rebuilt files and zip: no row, no name, no profile address, no source
    again = await b.build_all(str(w.tenant), str(w.run))
    assert again.status == "complete", again.errors
    assert not again.reused  # same people-free bytes would still be a new, honest version
    assert again.manifest is not None and again.manifest.exclusion_hash not in ("", "none")
    new_people = next(d for d in again.deliverables if d.kind == "people")
    new_zip = next(d for d in again.deliverables if d.kind == "workspace")
    assert new_people.version == people_file.version + 1
    assert name not in _everything(payloads.objects[new_people.object_key])
    z = _everything(payloads.objects[new_zip.object_key])
    assert name not in z and slug not in z
    with zipfile.ZipFile(io.BytesIO(payloads.objects[new_zip.object_key])) as zf:
        urls = json.loads(zf.read("build/urls.json"))
    assert not [u for u in urls.values() if slug in u]  # not in the sources list either

    # the web tables are made again under the new list
    rebuilt = await materialize.materialize(
        tenant_txn, b.reader, CFG, GRADING, w.tenant, w.run, datetime.now(UTC)
    )
    assert rebuilt.exclusion_hash == again.manifest.exclusion_hash
    assert rebuilt.counts["people"] == mat.counts["people"] - 1
    cells = fetch(
        "SELECT cells::text FROM view_rows WHERE run_id = %s AND table_kind = 'people'", w.run
    )
    assert not any(name in str(c[0]) for c in cells)

    async with tenant_txn(w.tenant) as s:
        assert await W.stale_kinds(s, w.account) == []
        assert {f["kind"] for f in await W.deliverables(s, w.account)} == {
            "summary",
            "people",
            "jobs",
            "tech",
            "evidence",
        }
        assert await SH.is_materialised(s, w.run)


async def test_removing_the_exclusion_lets_the_next_build_list_the_person_again() -> None:
    """The CURRENT list wins both ways: a person excluded and then allowed again is listed by
    the next build (the pull-time flag of the research is untouched)."""
    w, payloads = await seed_world()
    b = builder(w, payloads)
    name, profile = await _a_listed_person(b, w)
    await _exclude(w, people=(profile,))
    hidden = await b.build_all(str(w.tenant), str(w.run))
    people = next(d for d in hidden.deliverables if d.kind == "people")
    assert name not in _everything(payloads.objects[people.object_key])
    await _exclude(w)
    back = await b.build_all(str(w.tenant), str(w.run))
    people = next(d for d in back.deliverables if d.kind == "people")
    assert name in _everything(payloads.objects[people.object_key])


async def test_an_excluded_company_domain_is_a_forbidden_term_of_the_rebuild() -> None:
    w, payloads = await seed_world()
    b = builder(w, payloads)
    await _exclude(w, companies=("synthetic-competitor.example",))
    content = await b.reader.read(str(w.tenant), str(w.run))
    assert "synthetic-competitor.example" in content.forbidden_terms
    assert content.exclusion_hash not in ("", "none")


# ---------------------------------------------------------------------------------------- F2


def _add_run(w: World, status: str = "COMPLETED") -> World:
    """A second run of the same account (a resume or an incremental re-research)."""
    run = uuid4()
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "INSERT INTO runs (id, tenant_id, account_id, run_type, status, config_version,"
            " credit_cap, started_at) VALUES (%s, %s, %s, 'full', 'running', 'cfg', 100, now())",
            (run, w.tenant, w.account),
        )
        c.execute(
            "INSERT INTO account_runs (id, tenant_id, account_id, status, graph_version,"
            " budget_json, reserved_credits, idempotency_key, budget_usd, tool_call_cap,"
            " finished_at) VALUES (%s, %s, %s, %s, 'test', '{}', 1, %s, 5, 60, now())",
            (run, w.tenant, w.account, status, "k-" + uuid4().hex),
        )
    return World(w.tenant, w.account, run, w.domain)


def _fact_fp(c: Any, w: World, source: tuple[Any, str, str], value: str, fp: str, key: str) -> None:
    fid = uuid4()
    c.execute(
        "INSERT INTO facts (id, tenant_id, account_id, attribute_key, value_json, fingerprint,"
        " engine_grade, product_grade, read_in_primary_this_run, may_state_to_prospect, status,"
        " visibility, run_id, claim_kind, value_text, flags) VALUES (%s, %s, %s, %s,"
        " jsonb_build_object('text', %s::text), %s, 'A', 'confirmed', true, true, 'current',"
        " 'public', %s,"
        " 'sourced', %s, '[\"key_finding\"]'::jsonb)",
        (fid, w.tenant, w.account, key, value, fp, w.run, value),
    )
    c.execute(
        "INSERT INTO fact_evidence (tenant_id, fact_id, evidence_kind, url, supporting_sentence,"
        " span_sha256, source_id, source_tier, evidence_date, origin, provider_code) VALUES"
        " (%s, %s, 'source', %s, %s, %s, %s, 'T1', now(), 'full_page_read', 'p_search')",
        (
            w.tenant,
            fid,
            source[2],
            f"Sentence for {value}.",
            hashlib.sha256(value.encode()).hexdigest(),
            source[0],
        ),
    )


async def test_the_same_fact_recorded_under_two_runs_appears_once_everywhere() -> None:
    from account_intelligence.api.routes import crm

    w1 = make_world()
    _finish(w1)
    w2 = _add_run(w1)
    with psycopg.connect(admin_dsn()) as c, c.transaction():
        src = _source(c, w1, 1, "about")
        c.execute(
            "INSERT INTO account_source_counters (tenant_id, account_id, next_value)"
            " VALUES (%s, %s, 2)",
            (w1.tenant, w1.account),
        )
        fp = hashlib.sha256(b"same fact").hexdigest()
        for w in (w1, w2):  # the same finding found again by the second run
            _fact_fp(c, w, src, "Synthetic platform stack finding", fp, "tech.synthetic")
        _fact_fp(
            c, w2, src, "Synthetic news item", hashlib.sha256(b"other").hexdigest(), "news.item"
        )
        for w in (w1, w2):  # the same posting and person saved again under the second run
            c.execute(
                "INSERT INTO job_postings (tenant_id, account_id, run_id, posting_key, row_json,"
                " status, window_bucket, title, url) VALUES (%s, %s, %s, 'post-1', '{}'::jsonb,"
                " 'open', 'in_window', 'Salesforce Admin', 'https://example.test/jobs/1')",
                (w.tenant, w.account, w.run),
            )
            c.execute(
                "INSERT INTO people (tenant_id, account_id, run_id, source_ref, person_key, name,"
                " title, profile_url, excluded) VALUES (%s, %s, %s, 'S-0001', 'person-1',"
                " 'Synthetic Person', 'Chief Information Officer',"
                " 'https://profiles.example/in/synthetic-person', false)",
                (w.tenant, w.account, w.run),
            )
            c.execute(
                "INSERT INTO tech_detections (tenant_id, account_id, run_id, technology,"
                " source_ref) VALUES (%s, %s, %s, 'Salesforce', 'S-0001')",
                (w.tenant, w.account, w.run),
            )

    async with tenant_txn(w1.tenant) as s:
        out = await W.workspace(s, w1.account, USER, datetime.now(UTC))
        # counts: one people row (the person) + 0 people facts, one posting, one detection + one
        # tech fact, one news fact: never doubled by the second run
        assert out["counts"]["people"] == 1
        assert out["counts"]["jobs"] == 1
        assert out["counts"]["tech"] == 2
        assert out["counts"]["news"] == 1
        # the top findings: each fact once
        texts = [f["text"] for f in out["findings"]]
        assert len(texts) == len(set(texts)) == 2
        # the live sheets
        now = datetime.now(UTC)
        for sheet, expected in (("people", 1), ("jobs", 1), ("technology", 2), ("news", 1)):
            page = await SH.read(s, SH.parse(sheet, None, None, None), w1.account, USER, now)
            assert page["total"] == expected, (sheet, page["total"])
        # Ask's rows
        rows = await answers.load_workspace(s, USER)
        assert len([r for r in rows.facts if "stack finding" in r.text]) == 1
        assert len(rows.jobs) == 1 and len(rows.people) == 1 and len(rows.tech) == 1
        # the CRM items: one row per equal fact, newest first
        items = await crm._items(s, w1.account)
        assert len([i for i in items if "stack finding" in i.text]) == 1


# ---------------------------------------------------------------------------------------- F7


async def test_the_change_note_of_a_rebuild_compares_with_the_previous_run() -> None:
    w, payloads = await seed_world()
    b = builder(w, payloads)
    first = await b.build_all(str(w.tenant), str(w.run))
    assert first.status == "complete"
    store = b.store
    assert store is not None
    # the same run's own earlier set is not "the previous delivered version"
    assert await store.previous_manifest(str(w.tenant), str(w.account), str(w.run)) is None
    other = uuid4()
    assert await store.previous_manifest(str(w.tenant), str(w.account), str(other)) is not None


# ---------------------------------------------------------------------------------------- F5


async def test_a_users_rebuild_forbids_a_colleagues_private_account() -> None:
    """F5: the other accounts' names are forbidden terms, whoever asked for the build. A
    request runs in the caller's scope, which sees only the caller's own accounts; the reader
    lists the others in system scope."""
    from exports.readers.pg import PgContentReader

    w, payloads = await seed_world()
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "INSERT INTO accounts (tenant_id, owner_user_id, domain, name)"
            " VALUES (%s, 'a-colleague', 'colleague-private.example.org',"
            " 'Colleague Private Corp')",
            (w.tenant,),
        )
    with _as_user("test-owner"):  # an ordinary request: user scope, not the worker's
        content = await PgContentReader(payloads, batch=2).read(str(w.tenant), str(w.run))
    assert "Colleague Private Corp" in content.forbidden_terms
    assert "colleague-private.example.org" in content.forbidden_terms


# ------------------------------------------------------------------ orchestration review F8


def _gate(w: World, verdict: str, defects: list[dict[str, str]]) -> None:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "INSERT INTO run_gate_results (tenant_id, account_id, run_id, verdict, defects)"
            " VALUES (%s, %s, %s, %s, %s::jsonb)",
            (w.tenant, w.account, w.run, verdict, json.dumps(defects)),
        )


async def test_a_run_level_gate_failure_lists_and_serves_none_of_the_built_files() -> None:
    w, payloads = await seed_world()
    result = await builder(w, payloads).build_all(str(w.tenant), str(w.run))
    assert result.status == "complete"
    async with tenant_txn(w.tenant) as s:
        assert len(await W.deliverables(s, w.account)) == 5
    # a file-level defect holds back that file only
    _gate(w, "fail", [{"check": "leak_check", "item": "people:ngram=2"}])
    async with tenant_txn(w.tenant) as s:
        assert {f["kind"] for f in await W.deliverables(s, w.account)} == {
            "summary",
            "jobs",
            "tech",
            "evidence",
        }
    # a run-level defect (a finding without a source) holds back every file of the run
    _gate(w, "fail", [{"check": "fact_citations", "item": "f1:no_citation"}])
    async with tenant_txn(w.tenant) as s:
        assert await W.deliverables(s, w.account) == []
    # a later passing verdict (Resume reran the gate) lists them again
    _gate(w, "pass", [])
    async with tenant_txn(w.tenant) as s:
        assert len(await W.deliverables(s, w.account)) == 5


async def test_a_package_made_before_an_exclusion_is_stale_for_download() -> None:
    """The export packages a tenant made before it excluded someone are not served afterwards
    (``GET /v1/exports/{id}/files/{n}`` answers 409 ``package_stale``); the check is whether the
    exclusion set in force when the package was made is still the current one."""
    from datetime import timedelta

    w = make_world()
    t0 = datetime.now(UTC) - timedelta(days=3)
    await _exclude(w, at=t0)  # version 1: nobody excluded
    await _exclude(w, people=("Jane Roe",), at=t0 + timedelta(days=1))  # version 2
    async with tenant_txn(w.tenant) as s:
        assert await materialize.exclusions_changed_since(s, t0 + timedelta(hours=1))
        assert await materialize.exclusions_changed_since(s, t0 - timedelta(days=1))
        assert not await materialize.exclusions_changed_since(s, t0 + timedelta(days=2))
