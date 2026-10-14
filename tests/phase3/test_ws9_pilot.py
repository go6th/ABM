"""WS9 pilot feedback and seller usefulness (PL1, PL2, D-68). SYNTHETIC tenants and rows only.

Lying scenarios: feedback with an invented reason or row, a note that reaches the logs or the
audit, an event naming an invented subject, a non-Confirmed fact or another tenant's fact counted
as referenced, a repeat that counts twice, a rate shown without its base, an operator aggregate
that carries content.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.api.routes.exports import _carries_facts
from account_intelligence.db.session import tenant_txn, untenanted_session
from pilot import metric, usefulness
from tests.conftest import ADMIN_DSN
from tests.fakes.phase3 import FakeStaffIdp
from tests.phase3.conftest import Browser, Plane, add_member, add_oidc_connection, new_tenant, uid

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
NOTE = "SYNTHETIC-NOTE-the-champion-left-in-march"


@dataclass
class Planted:
    tenant: UUID
    domain: str
    run: UUID
    questions: list[UUID]  # newest report version
    old_question: UUID  # an earlier version of the same run
    confirmed: UUID
    probable: UUID


def _db(sql: str, *params: Any) -> list[tuple[Any, ...]]:
    with psycopg.connect(ADMIN_DSN) as c:
        return c.execute(sql, params).fetchall()


def _plant(tenant: UUID, domain: str) -> Planted:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        acc = c.execute(
            # owned by the signed-in member (private workspaces, migration 0063)
            "INSERT INTO accounts (tenant_id, owner_user_id, domain) VALUES (%s, coalesce((SELECT"
            " user_id FROM memberships WHERE tenant_id = %s LIMIT 1), 'test-owner'), %s)"
            " RETURNING id",
            (tenant, tenant, domain),
        ).fetchone()[0]
        run = c.execute(
            "INSERT INTO account_runs (tenant_id, account_id, status, graph_version, budget_json,"
            " reserved_credits, idempotency_key, budget_usd, finished_at) VALUES (%s, %s,"
            " 'COMPLETED', 'g', '{}', 1, %s, 5, now()) RETURNING id",
            (tenant, acc, uuid4().hex),
        ).fetchone()[0]

        def fact(grade: str) -> UUID:
            with c.transaction():  # no fact without evidence at commit
                fid: UUID = c.execute(
                    "INSERT INTO facts (tenant_id, account_id, attribute_key, value_json,"
                    " fingerprint, product_grade, read_in_primary_this_run,"
                    " may_state_to_prospect) VALUES (%s, %s, 'k', '{}', %s, %s, %s, %s)"
                    " RETURNING id",
                    (tenant, acc, uuid4().hex, grade, grade == "confirmed", grade == "confirmed"),
                ).fetchone()[0]
                c.execute(
                    "INSERT INTO fact_evidence (tenant_id, fact_id, evidence_kind,"
                    " supporting_sentence, call_ref) VALUES (%s, %s, 'search_proof',"
                    " 'synthetic fixture evidence', 'fixture-call')",
                    (tenant, fid),
                )
            return fid

        confirmed, probable = fact("confirmed"), fact("probable")

        def section(version: int, position: int) -> UUID:
            sid: UUID = c.execute(
                "INSERT INTO report_sections (tenant_id, account_id, run_id, version, kind,"
                " position, text) VALUES (%s, %s, %s, %s, 'first_call_question', %s,"
                " 'SYNTHETIC question?') RETURNING id",
                (tenant, acc, run, version, position),
            ).fetchone()[0]
            return sid

        old = section(1, 0)
        questions = [section(2, 0), section(2, 1)]
        for pos, f in enumerate((confirmed, probable), start=1):
            c.execute(
                "INSERT INTO view_rows (tenant_id, run_id, account_id, table_kind, position, ref,"
                " row_id, cells, source_state) VALUES (%s, %s, %s, 'facts', %s, %s, %s,"
                " '{}', 'no_source')",
                (tenant, run, acc, pos, f"F-{pos}", str(f)),
            )
    return Planted(tenant, domain, run, questions, old, confirmed, probable)


def _member(tenant: UUID) -> str:
    """The signed-in member (events are that user's own, private workspaces)."""
    with psycopg.connect(ADMIN_DSN) as c:
        return str(
            c.execute("SELECT user_id FROM memberships WHERE tenant_id = %s", (tenant,)).fetchone()[
                0
            ]
        )


async def _admin(plane: Plane, browser: Browser, name: str) -> UUID:
    t = new_tenant(name)
    slug = f"acme-{uid()}"
    add_oidc_connection(t, plane, slug)
    add_member(t, f"{slug}:root", "admin")
    await browser.oidc_sign_in(slug, "root", extra={"groups": ["admins"]})
    return t


async def test_feedback_is_tenant_data_with_a_fixed_reason_list(
    plane: Plane, browser: Browser, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    t = await _admin(plane, browser, "pl-feedback")
    p = _plant(t, f"fb-{uid()}.example")
    post = browser.call
    ok = await post(
        "POST",
        "/v1/feedback",
        json={"domain": p.domain, "verdict": "useful", "reason": "used_on_call", "note": NOTE},
    )
    assert ok.status_code == 201, ok.text
    for bad in (
        {"verdict": "useful", "reason": "invented"},
        {"verdict": "useful", "reason": "wrong"},  # a not-useful reason on a useful verdict
        {"verdict": "not_useful", "reason": "wrong", "table": "facts", "row_id": "nope"},
        {"verdict": "not_useful", "reason": "wrong", "row_id": str(p.confirmed)},  # no table
    ):
        r = await post("POST", "/v1/feedback", json={"domain": p.domain, **bad})
        assert r.status_code == 422, (bad, r.text)
    row = await post(
        "POST",
        "/v1/feedback",
        json={
            "domain": p.domain,
            "verdict": "not_useful",
            "reason": "out_of_date",
            "table": "facts",
            "row_id": str(p.confirmed),
        },
    )
    assert row.status_code == 201, row.text
    changed = await post(
        "POST",
        "/v1/feedback",
        json={"domain": p.domain, "verdict": "not_useful", "reason": "already_known"},
    )
    assert changed.status_code == 201
    listed = (await browser.client.get("/v1/feedback", params={"domain": p.domain})).json()
    current = {(f["scope"], f["verdict"], f["reason"]) for f in listed["feedback"]}
    assert current == {
        ("account", "not_useful", "already_known"),
        ("row", "not_useful", "out_of_date"),
    }
    assert _db("SELECT count(*) FROM run_feedback WHERE tenant_id = %s", t) == [(3,)]  # kept
    # the note is tenant data: stored, never logged, never audited
    assert _db("SELECT note FROM run_feedback WHERE tenant_id = %s AND note IS NOT NULL", t) == [
        (NOTE,)
    ]
    assert NOTE not in caplog.text
    assert not _db(
        "SELECT 1 FROM audit_events WHERE tenant_id = %s AND row(audit_events)::text LIKE %s",
        t,
        f"%{NOTE}%",
    )
    # another tenant sees nothing of it
    other = Browser(plane)
    try:
        await _admin(plane, other, "pl-feedback-other")
        assert (await other.client.get("/v1/feedback")).json()["feedback"] == []
        assert (
            await other.call(
                "POST",
                "/v1/feedback",
                json={"domain": p.domain, "verdict": "useful", "reason": "accurate"},
            )
        ).status_code == 404
    finally:
        await other.close()


async def test_usefulness_events_are_identifiers_only_and_counted_once(
    plane: Plane, browser: Browser
) -> None:
    t = await _admin(plane, browser, "pl-useful")
    p = _plant(t, f"use-{uid()}.example")
    q = {"kind": "first_call_question_used", "subject_id": str(p.questions[0])}
    first = await browser.call("POST", "/v1/usefulness", json={**q, "via": "marked"})
    again = await browser.call("POST", "/v1/usefulness", json={**q, "via": "marked"})
    assert (first.json(), again.json()) == ({"recorded": True}, {"recorded": False})
    assert (await browser.call("POST", "/v1/usefulness", json={**q, "via": "copied"})).json()[
        "recorded"
    ]
    async with tenant_txn(t) as s:  # the next day counts again (a session would have expired)
        user = _db("SELECT user_id FROM usefulness_events WHERE tenant_id = %s LIMIT 1", t)[0][0]
        assert await usefulness.record(
            s,
            kind=usefulness.QUESTION,
            subject=p.questions[0],
            via="marked",
            user_id=user,
            now=plane.clock.now() + timedelta(days=1),
        )
    fact = {"kind": "confirmed_fact_referenced", "via": "opened"}
    assert (
        await browser.call("POST", "/v1/usefulness", json={**fact, "subject_id": str(p.confirmed)})
    ).status_code == 202
    stranger = _plant(new_tenant("pl-useful-other"), f"x-{uid()}.example")
    for subject, body in (
        (uuid4(), fact),  # invented
        (p.probable, fact),  # not Confirmed
        (p.questions[0], fact),  # a question is not a fact
        (p.confirmed, {"kind": "first_call_question_used", "via": "marked"}),
        (stranger.confirmed, fact),  # another tenant's fact
        (stranger.questions[0], {"kind": "first_call_question_used", "via": "copied"}),
    ):
        r = await browser.call("POST", "/v1/usefulness", json={**body, "subject_id": str(subject)})
        assert r.status_code == 404, (subject, body)
    mismatch = await browser.call("POST", "/v1/usefulness", json={**q, "via": "opened"})
    assert mismatch.status_code == 422
    exported = await browser.call("POST", "/v1/usefulness", json={**fact, "via": "exported"})
    assert exported.status_code == 422  # only a real download records an export reference
    cols = {
        r[0]
        for r in _db(
            "SELECT column_name FROM information_schema.columns WHERE table_name ="
            " 'usefulness_events'"
        )
    }
    assert cols == {
        "id",
        "tenant_id",
        "kind",
        "subject_id",
        "account_id",
        "via",
        "user_id",
        "dedupe_key",
        "at",
        "owner_user_id",
    }
    # a downloaded export references the run's Confirmed facts once a day, never others
    async with tenant_txn(t) as s:
        n1 = await usefulness.exported(
            s, run_id=p.run, user_id="u", now=plane.clock.now(), limit=500
        )
        n2 = await usefulness.exported(
            s, run_id=p.run, user_id="u", now=plane.clock.now(), limit=500
        )
    assert (n1, n2) == (1, 0)
    assert _carries_facts("files", {"kind": "summary"}) and _carries_facts("pdf", {"kind": "x"})
    assert _carries_facts("csv", {"kind": "facts"})
    assert not _carries_facts("files", {"kind": "people"})
    assert not _carries_facts("csv", {"kind": "jobs"})


async def test_the_usefulness_metric_carries_its_base(plane: Plane, browser: Browser) -> None:
    t = await _admin(plane, browser, "pl-metric")
    p = _plant(t, f"m-{uid()}.example")
    for subject in (p.questions[0], p.old_question):
        async with tenant_txn(t) as s:
            await usefulness.record(
                s,
                kind=usefulness.QUESTION,
                subject=subject,
                via="marked",
                user_id=_member(t),
                now=plane.clock.now(),
            )
    await browser.call(
        "POST",
        "/v1/feedback",
        json={"domain": p.domain, "verdict": "useful", "reason": "saved_time", "note": NOTE},
    )
    got = (await browser.client.get("/v1/usefulness/metric")).json()
    # the old version's question is neither delivered nor counted
    assert got["question_use"] == {"used": 1, "base": 2, "small_base": True, "rate": 0.5}
    assert got["confirmed_fact_reference"] == {
        "used": 0,
        "base": 1,
        "small_base": True,
        "rate": 0.0,
    }
    assert got["feedback"] == [{"verdict": "useful", "reason": "saved_time", "count": 1}]
    assert got["status"] == "proposal"
    assert metric._rate(0, 0, 20) == {"used": 0, "base": 0, "small_base": True, "rate": None}
    assert metric._rate(10, 20, 20)["small_base"] is False
    # the operator aggregate is counts per tenant: no subject, no note, no domain
    plane.app.state.container.tenant_plane.staff_verifier = FakeStaffIdp()
    agg = await browser.client.get(
        "/operator/v1/pilot/aggregate", headers={"Authorization": "Bearer operator:op-1"}
    )
    assert agg.status_code == 200
    mine = next(x for x in agg.json()["tenants"] if x["tenant_id"] == str(t))
    assert mine == {
        "tenant_id": str(t),
        "questions_used": 2,
        "facts_referenced": 0,
        "useful": 1,
        "not_useful": 0,
    }
    for secret in (NOTE, p.domain, str(p.questions[0]), str(p.confirmed), "SYNTHETIC question"):
        assert secret not in agg.text
    support = await browser.client.get(
        "/operator/v1/pilot/aggregate", headers={"Authorization": "Bearer support:s-1"}
    )
    assert support.status_code == 403
    async with untenanted_session() as plain:
        assert any(
            r["tenant_id"] == str(t)
            for r in await metric.operator_aggregate(plain, plane.clock.now())
        )


async def test_the_seed_script_builds_two_synthetic_pilot_tenants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import importlib.util
    from datetime import UTC, datetime
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "seed_pilot", Path(__file__).resolve().parents[2] / "scripts" / "seed_pilot.py"
    )
    assert spec and spec.loader
    seed_pilot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed_pilot)
    monkeypatch.setenv("APP_ENV", "production")
    assert seed_pilot.main([]) == 2  # never outside dev, local or test
    with pytest.raises(ValueError):
        seed_pilot.plan_tenants((2, 5))
    made = await seed_pilot.seed((3, 5), datetime.now(UTC))
    assert [len(t["domains"]) for t in made] == [3, 5]
    for t in made:
        rows = _db(
            "SELECT domain FROM accounts WHERE tenant_id = %s ORDER BY domain", t["tenant_id"]
        )
        assert [r[0] for r in rows] == sorted(t["domains"])
        assert all(d.endswith(".example") for (d,) in rows)
        assert _db(
            "SELECT plan_code FROM plan_assignments WHERE tenant_id = %s", t["tenant_id"]
        ) == [("pilot",)]
        assert _db(
            "SELECT used FROM usage_counters WHERE tenant_id = %s AND dimension = 'accounts'",
            t["tenant_id"],
        ) == [(len(t["domains"]),)]
        assert _db("SELECT count(*) FROM account_runs WHERE tenant_id = %s", t["tenant_id"]) == [
            (0,)
        ]  # nothing is run
