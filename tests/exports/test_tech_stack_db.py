"""The organisation tech stack on real Postgres (migration 0090): the pipeline step stores one
row per (run, business function, category, technology) in the lookup's order, once; the table is
insert-only, owned like the account, and no other tenant or user of the tenant reads it; the step
makes no call of its own (a run that never pulled the stack stores nothing). All data synthetic."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import errors
from sqlalchemy import text

from account_intelligence.connectors.models import OrganizationRecord, TechStackRecord
from account_intelligence.db.session import tenant_txn
from agents.production import pipelines
from agents.production.pipelines import RunPipelines
from tests.tracker.world import World, admin_dsn, fetch, make_world

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]


def _item(
    function: str, category: str, technology: str, postings: int, ref: str = "raw-1"
) -> TechStackRecord:
    """A stack record as the lookup parses it (built without the provenance machinery)."""
    return TechStackRecord.model_construct(
        kind="tech_stack_item",
        provenance=SimpleNamespace(raw_ref=ref),
        lineage_hint=None,
        organization_id=7,
        business_function=function,
        category_slug=category.lower().replace(" ", "-"),
        category_name=category,
        technology_name=technology,
        technology_slug=technology.lower().replace(" ", "-"),
        job_post_used_count=postings,
    )


RECORDS = [
    _item("Sales", "Customer Relationship Manager (CRM)", "Salesforce", 1769),
    _item("Sales", "Customer Relationship Manager (CRM)", "SAP CRM", 4),
    _item("Finance", "ERP and Business Management Software", "SAP", 2383),
    _item("Sales", "Customer Relationship Manager (CRM)", "Salesforce", 99),  # a repeat
    _item("Finance", "ERP and Business Management Software", "  ", 5),  # no name
]


def _deps(w: World, connectors: object | None = object()) -> Any:
    return SimpleNamespace(
        txn=tenant_txn,
        tenant_id=w.tenant,
        account_id=w.account,
        run_id=w.run,
        domain=w.domain,
        connectors=connectors,
        call_context=lambda: SimpleNamespace(),
    )


def _step(w: World, connectors: object | None = object()) -> RunPipelines:
    return RunPipelines(_deps(w, connectors), grading=None)


def _rows(w: World) -> list[tuple[Any, ...]]:
    return fetch(
        "SELECT business_function, category, technology, postings, position, owner_user_id"
        " FROM tech_stack_items WHERE run_id = %s ORDER BY position",
        w.run,
    )


def _as_app_rw(
    tenant: UUID, sql: str, *params: object, user: str | None = None, scope: str = "system"
) -> list[Any]:
    from tests.conftest import APP_DSN

    with psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.scope', %s, true),"
            " set_config('app.user_id', %s, true)",
            (str(tenant), scope, user or ""),
        )
        cur = c.execute(sql, params)
        return cur.fetchall() if cur.description else []


async def test_the_step_stores_each_item_once_in_the_lookups_order() -> None:
    w = make_world()
    step = _step(w)
    written, skipped = await step._insert_stack(RECORDS)
    assert written == 3  # the repeat and the nameless item are not stored
    assert skipped == 1  # the nameless item is counted, never dropped silently (review 24)
    rows = _rows(w)
    assert [r[:5] for r in rows] == [
        ("Sales", "Customer Relationship Manager (CRM)", "Salesforce", 1769, 0),
        ("Sales", "Customer Relationship Manager (CRM)", "SAP CRM", 4, 1),
        ("Finance", "ERP and Business Management Software", "SAP", 2383, 2),
    ]
    assert {r[5] for r in rows} == {"test-owner"}  # owned like the account
    # a replay (a resumed slice) writes nothing twice
    assert await step._insert_stack(RECORDS) == (0, 1)
    assert len(_rows(w)) == 3


async def test_the_rows_name_the_accounts_owner_whoever_writes_them() -> None:
    """A worker in system scope may carry another acting user (or none): the stack still belongs
    to the account's owner, as the link archive does (security review 2026-10-29, F14)."""
    from account_intelligence.db.session import _ACTOR

    w = make_world()
    token = _ACTOR.set("someone-else")
    try:
        written, _ = await _step(w)._insert_stack(RECORDS[:2])
    finally:
        _ACTOR.reset(token)
    assert written == 2
    assert {r[5] for r in _rows(w)} == {"test-owner"}


async def test_the_database_refuses_blank_negative_and_repeated_rows_and_any_change() -> None:
    w = make_world()
    await _step(w)._insert_stack(RECORDS[:1])
    insert = (
        "INSERT INTO tech_stack_items (account_id, run_id, business_function, category,"
        " technology, postings, position) VALUES (%s, %s, %s, %s, %s, %s, 0)"
    )
    owner = {"user": "test-owner"}
    with pytest.raises(errors.CheckViolation):
        _as_app_rw(w.tenant, insert, w.account, w.run, "Sales", "CRM", " ", 1, **owner)
    with pytest.raises(errors.CheckViolation):
        _as_app_rw(w.tenant, insert, w.account, w.run, "Sales", "CRM", "Tool", -1, **owner)
    with pytest.raises(errors.UniqueViolation):
        _as_app_rw(
            w.tenant,
            insert,
            w.account,
            w.run,
            "Sales",
            "Customer Relationship Manager (CRM)",
            "Salesforce",
            1,
            **owner,
        )
    for sql in (
        "UPDATE tech_stack_items SET postings = 1 WHERE run_id = %s",
        "DELETE FROM tech_stack_items WHERE run_id = %s",
    ):
        with pytest.raises(errors.InsufficientPrivilege):
            _as_app_rw(w.tenant, sql, w.run, **owner)


async def test_another_tenant_and_another_user_never_read_the_rows() -> None:
    a, b = make_world(), make_world()
    await _step(a)._insert_stack(RECORDS[:3])
    # another client: nothing, and it cannot write into the first client's account
    async with tenant_txn(b.tenant) as s:
        n = (
            await s.execute(
                text("SELECT count(*) FROM tech_stack_items WHERE run_id = :r"), {"r": a.run}
            )
        ).scalar()
    assert n == 0
    with pytest.raises(errors.InsufficientPrivilege):
        _as_app_rw(
            b.tenant,
            "INSERT INTO tech_stack_items (tenant_id, owner_user_id, account_id, run_id,"
            " business_function, category, technology) VALUES (%s, 'x', %s, %s, 'a', 'b', 'c')",
            a.tenant,
            a.account,
            a.run,
            user="x",
            scope="user",
        )
    count = "SELECT count(*) FROM tech_stack_items WHERE run_id = %s"
    # the owner sees the rows, another user of the same client sees none, the system sees all
    assert _as_app_rw(a.tenant, count, a.run, user="test-owner", scope="user") == [(3,)]
    assert _as_app_rw(a.tenant, count, a.run, user="someone-else", scope="user") == [(0,)]
    assert _as_app_rw(a.tenant, count, a.run) == [(3,)]
    # another user cannot write a row stamped with the owner either
    with pytest.raises(errors.InsufficientPrivilege):
        _as_app_rw(
            a.tenant,
            "INSERT INTO tech_stack_items (owner_user_id, account_id, run_id, business_function,"
            " category, technology) VALUES ('test-owner', %s, %s, 'a', 'b', 'c')",
            a.account,
            a.run,
            user="someone-else",
            scope="user",
        )


async def test_the_step_makes_no_call_when_the_run_never_pulled_the_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = make_world()
    calls: list[str] = []

    async def fake_call(op: Any, *_a: Any, **_k: Any) -> Any:
        calls.append(str(op))
        raise AssertionError("a new paid call")

    monkeypatch.setattr(pipelines, "call_operation", fake_call)
    out = await _step(w).write_stack(SimpleNamespace())  # type: ignore[arg-type]
    assert out.exits == {"stack": "not_pulled"} and out.counters == {"stack_rows": 0}
    assert calls == [] and _rows(w) == []
    # no provider configured: nothing happens either
    out = await _step(w, connectors=None).write_stack(SimpleNamespace())  # type: ignore[arg-type]
    assert out.exits == {"stack": "not_available_yet"}


def _ledger_stack_call(w: World, run: UUID | None = None) -> None:
    with psycopg.connect(admin_dsn(), autocommit=True) as c:
        c.execute(
            "INSERT INTO tool_calls (tenant_id, run_id, tool, args_hash, response, cost_usd,"
            " provider_code, operation, cost_source) VALUES (%s, %s, 'p_org:org_tech_stack', %s,"
            " '{}', 0, 'p_org', 'org_tech_stack', 'documented')",
            (w.tenant, run or w.run, uuid4().hex + uuid4().hex),
        )


async def test_the_step_replays_the_lookup_the_run_made_and_stores_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w = make_world()
    _ledger_stack_call(w)
    seen: list[Any] = []

    async def fake_call(op: Any, args: Any, *_a: Any, **_k: Any) -> Any:
        seen.append((str(op), args))
        if "resolve" in str(op).lower():
            org = OrganizationRecord.model_construct(
                kind="organization", matched=True, organization_id=7
            )
            return SimpleNamespace(records=[org])
        return SimpleNamespace(records=RECORDS)

    monkeypatch.setattr(pipelines, "call_operation", fake_call)
    out = await _step(w).write_stack(SimpleNamespace())  # type: ignore[arg-type]
    assert out.exits == {"stack": "written"}
    assert out.counters == {"stack_rows": 3, "stack_records": 5, "stack_skipped_empty": 1}
    assert [type(a).__name__ for _op, a in seen] == ["OrgResolveArgs", "OrgTechStackArgs"]
    assert seen[1][1].organization_id == 7
    assert len(_rows(w)) == 3
    # an unmatched domain stores nothing
    w2 = make_world()
    _ledger_stack_call(w2)

    async def no_match(op: Any, *_a: Any, **_k: Any) -> Any:
        return SimpleNamespace(
            records=[
                OrganizationRecord.model_construct(
                    kind="organization", matched=False, organization_id=None
                )
            ]
        )

    monkeypatch.setattr(pipelines, "call_operation", no_match)
    out = await _step(w2).write_stack(SimpleNamespace())  # type: ignore[arg-type]
    assert out.exits == {"stack": "not_matched"} and _rows(w2) == []


async def test_the_step_uses_the_first_matched_organisation_that_has_an_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 1 looks the stack up under the first matched record WITH an id; so does the step
    (a matched record without one in front of it is no reason to find no stack)."""
    w = make_world()
    _ledger_stack_call(w)
    seen: list[Any] = []

    async def fake_call(op: Any, args: Any, *_a: Any, **_k: Any) -> Any:
        seen.append(args)
        if "resolve" in str(op).lower():
            return SimpleNamespace(
                records=[
                    OrganizationRecord.model_construct(
                        kind="organization", matched=True, organization_id=None
                    ),
                    OrganizationRecord.model_construct(
                        kind="organization", matched=True, organization_id=9
                    ),
                ]
            )
        return SimpleNamespace(records=RECORDS[:1])

    monkeypatch.setattr(pipelines, "call_operation", fake_call)
    out = await _step(w).write_stack(SimpleNamespace())  # type: ignore[arg-type]
    assert out.exits == {"stack": "written"}
    assert seen[1].organization_id == 9
