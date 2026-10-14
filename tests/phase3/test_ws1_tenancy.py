"""WS1 tenancy on real PostgreSQL: versioned settings (TN5), exclusions at pull time, the
append-only tenant lifecycle and the deletion cascade (AD2). SYNTHETIC tenants only.

Lying scenarios: a settings writer that tries to overwrite a version, a racing second save, a
racing suspension, a requester approving their own deletion, an erase call without approval, an
erase call naming a ledger table, a plan that lists the audit table.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from hypothesis import given
from hypothesis import strategies as st

from account_intelligence.db.session import tenant_txn
from tenancy import lifecycle, settings
from tenancy.settings import (
    ExclusionFilter,
    Exclusions,
    PeopleScope,
    SettingsConflict,
    SettingsError,
    TenantSettings,
    normalise_person,
)
from tests.conftest import ADMIN_DSN, APP_DSN
from tests.isolation.matrix.plant import plant, tenant_tables

pytestmark = pytest.mark.requires_postgres
NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def _tenant(name: str = "ws1") -> UUID:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        tid: UUID = c.execute(
            "INSERT INTO tenants (name) VALUES (%s) RETURNING id", (f"{name}-synthetic",)
        ).fetchone()[0]
    return tid


# ---------------------------------------------------------------- settings (TN5)


def test_settings_validation_refuses_what_the_platform_does_not_know() -> None:
    base = settings.default_settings()
    assert base.platform_lanes and base.people_scope.levels_in_scope
    bad: list[dict[str, Any]] = [
        {"platform_lanes": ["not_a_lane"], "people_scope": {"levels_in_scope": ["VP"]}},
        {"platform_lanes": [], "people_scope": {"levels_in_scope": ["VP"]}},
        {"platform_lanes": ["sap", "sap"], "people_scope": {"levels_in_scope": ["VP"]}},
        {"platform_lanes": ["sap"], "people_scope": {"levels_in_scope": ["Intern"]}},
        {
            "platform_lanes": ["sap"],
            "people_scope": {"levels_in_scope": ["VP"]},
            "exclusions": {"companies": ["not a domain"]},
        },
        {
            "platform_lanes": ["sap"],
            "people_scope": {"levels_in_scope": ["VP"]},
            "exclusions": {"companies": [f"c{i}.example" for i in range(201)]},
        },
        {
            "platform_lanes": ["sap"],
            "people_scope": {"levels_in_scope": ["VP"]},
            "pursuing_client": "x" * 121,
        },
        {
            "platform_lanes": ["sap"],
            "people_scope": {"levels_in_scope": ["VP"]},
            "tenant_id": str(uuid4()),
        },
        {
            "platform_lanes": ["sap"],
            "people_scope": {"levels_in_scope": ["VP"]},
            "branding": {"display_name": "X", "accent_hex": "red"},
        },
    ]
    for data in bad:
        with pytest.raises(SettingsError):
            settings.parse(data)


def test_settings_hash_is_canonical() -> None:
    a = TenantSettings(
        platform_lanes=("sap", "veeva"),
        people_scope=PeopleScope(levels_in_scope=("VP",)),
        exclusions=Exclusions(companies=("B.example", "a.example"), people=("Ann Lee",)),
    )
    b = TenantSettings(
        platform_lanes=("sap", "veeva"),
        people_scope=PeopleScope(levels_in_scope=("VP",)),
        exclusions=Exclusions(
            companies=("https://www.a.example/", "b.example"), people=("ann  lee",)
        ),
    )
    assert settings.settings_hash(a) == settings.settings_hash(b)


@pytest.mark.asyncio
async def test_settings_versions_are_insert_only_and_conflict_safe() -> None:
    t = _tenant()
    async with tenant_txn(t) as s:
        assert (await settings.current(s)).version == 0
        v1 = await settings.ensure_saved(s, actor="admin-1", now=NOW)
        assert v1.version == 1
        changed = v1.settings.model_copy(update={"pursuing_client": "Synthetic Client"})
        v2 = await settings.save(s, changed, actor="admin-1", expected_version=1, now=NOW)
        assert v2.version == 2 and (await settings.current(s)).settings.pursuing_client
        with pytest.raises(SettingsConflict):
            await settings.save(s, changed, actor="admin-2", expected_version=1, now=NOW)
    # a racing writer on its own connection loses cleanly
    results = await asyncio.gather(
        *(_save_once(t, f"admin-{i}") for i in range(5)), return_exceptions=True
    )
    assert sum(1 for r in results if r == "saved") == 1
    assert all(r == "saved" or isinstance(r, SettingsConflict) for r in results)
    for sql in (
        "UPDATE tenant_settings_versions SET created_by = 'x'",
        "DELETE FROM tenant_settings_versions",
    ):
        with psycopg.connect(APP_DSN) as c, c.transaction():
            c.execute(
                "SELECT set_config('app.tenant_id', %s, true),"
                " set_config('app.scope', 'system', true)",
                (str(t),),
            )
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)


async def _save_once(t: UUID, actor: str) -> str:
    async with tenant_txn(t) as s:
        cur = await settings.current(s)
        await settings.save(s, cur.settings, actor=actor, expected_version=2, now=NOW)
        await asyncio.sleep(0.05)
    return "saved"


@pytest.mark.asyncio
async def test_a_run_records_the_settings_version_once() -> None:
    t = _tenant()
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        acc = c.execute(
            "INSERT INTO accounts (tenant_id, owner_user_id, domain)"
            " VALUES (%s, 'test-owner', 'pin.example') RETURNING id",
            (t,),
        ).fetchone()[0]
        run = c.execute(
            "INSERT INTO account_runs (tenant_id, account_id, graph_version, budget_json,"
            " reserved_credits, idempotency_key) VALUES (%s, %s, 'g', '{}', 1, %s) RETURNING id",
            (t, acc, uuid4().hex),
        ).fetchone()[0]
    async with tenant_txn(t) as s:
        v = await settings.ensure_saved(s, actor="admin-1", now=NOW)
        assert await settings.pin_run(s, run, v.version)
        assert not await settings.pin_run(s, run, v.version)  # recorded once, never moved


# ---------------------------------------------------------------- exclusions (TN5 pull time)


def test_exclusion_filter_normalises_both_sides() -> None:
    rule = ExclusionFilter.of(
        TenantSettings(
            platform_lanes=("sap",),
            people_scope=PeopleScope(levels_in_scope=("VP",)),
            exclusions=Exclusions(
                companies=("partner.example",),
                people=("https://www.profiles.example/in/ann-lee/", "Bob  ÖSTER"),
            ),
        )
    )
    assert rule.excludes_company("partner.example")
    assert rule.excludes_company("https://eu.partner.example/path")
    assert not rule.excludes_company("notpartner.example")
    assert not rule.excludes_company(None) and not rule.excludes_company("bad domain")
    assert rule.excludes_person(profile_url="http://profiles.example/in/ann-lee?x=1")
    assert rule.excludes_person(name="bob oster")
    assert not rule.excludes_person(name="Bob Osterberg")
    assert not rule.excludes_person()
    assert rule.run_input()["excluded_companies"] == ["partner.example"]
    for bad in ("", "   ", "https://x", "!!!"):
        with pytest.raises(SettingsError):
            normalise_person(bad)


@pytest.mark.asyncio
async def test_exclusions_hold_at_pull_and_render_time() -> None:
    """Pull time: the people pipeline marks a tenant-excluded person excluded, so the people
    export never lists them (render time is covered again by the row sources, WS4)."""
    from people.export import build_export
    from tests.fakes.people import Corpus, FakeRunInput
    from tests.people.kit import Kit

    corpus = Corpus()
    first = await Kit(corpus).run()
    listed = [p for p in first.people if p.classification.status == "in_scope"]
    assert listed, "the synthetic corpus must list someone in scope"
    target = listed[0]
    excluded_input = FakeRunInput(corpus)
    excluded_input.excluded = (normalise_person(str(target.profile_url)),)
    second = await Kit(Corpus()).run(excluded_input)
    hit = [p for p in second.people if p.profile_url == target.profile_url]
    assert hit and all(p.classification.status in ("excluded", "merged") for p in hit)
    assert any(p.classification.reason == "tenant_exclusion" for p in hit)
    assert second.counts is not None
    exported = build_export(
        second.people, second.counts, second.count_sentences, second.source_codes
    )
    assert exported.rows and all(r.profile_url != target.profile_url for r in exported.rows)


# ---------------------------------------------------------------- lifecycle (AD2)


@pytest.mark.asyncio
async def test_lifecycle_changes_are_append_only_and_race_safe() -> None:
    t = _tenant()
    async with tenant_txn(t) as s:
        st = await lifecycle.suspend(
            s, actor="op-1", plane="operator", reason_code="billing", now=NOW
        )
        assert st.suspended
        with pytest.raises(lifecycle.LifecycleError):
            await lifecycle.suspend(
                s, actor="op-1", plane="operator", reason_code="billing", now=NOW
            )
        st = await lifecycle.reactivate(
            s, actor="op-1", plane="operator", reason_code="paid", now=NOW
        )
        assert not st.suspended

    # two racing suspensions from the same state: exactly one applies
    async def race(actor: str) -> str:
        async with tenant_txn(t) as s:
            st = await lifecycle.state(s)
            await asyncio.sleep(0.05)
            await lifecycle.append(
                s, st, "suspended", actor=actor, plane="operator", now=NOW, reason_code="race"
            )
        return "ok"

    results = await asyncio.gather(race("op-a"), race("op-b"), return_exceptions=True)
    assert results.count("ok") == 1
    assert any(isinstance(r, lifecycle.LifecycleConflict) for r in results)
    with psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.scope', 'system', true)",
            (str(t),),
        )
        for sql in ("UPDATE tenant_events SET actor = 'x'", "DELETE FROM tenant_events"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
                c.execute(sql)


def _plant_erasable(t: UUID, plan: lifecycle.DeletionPlan) -> None:
    import yaml

    with open("tests/isolation/matrix/matrix.yaml") as f:
        matrix = yaml.safe_load(f)["tables"]
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        tables = {tb.name: tb for tb in tenant_tables(admin)}
        for name in (*plan.erase, "sources", "wallet_ledger"):
            over = (matrix.get(name) or {}).get("plant")
            for _ in range(3):
                plant(admin, tables[name], t, over)


def _count(t: UUID, table: str) -> int:
    with psycopg.connect(ADMIN_DSN) as c:
        n: int = c.execute(
            f"SELECT count(*) FROM {table} WHERE tenant_id = %s",
            (t,),  # noqa: S608
        ).fetchone()[0]
    return n


@pytest.mark.asyncio
async def test_deletion_dry_run_lists_exactly_what_execution_removes() -> None:
    plan = lifecycle.DeletionPlan(
        erase=lifecycle.load_plan().erase,
        blocked=lifecycle.load_plan().blocked,
        never=lifecycle.load_plan().never,
        batch_size=2,  # small batches: several per table, and the cap is reached
        max_batches_per_pass=5,
    )
    a, b = _tenant("del-a"), _tenant("del-b")
    _plant_erasable(a, plan)
    _plant_erasable(b, plan)
    ledger_before = _count(a, "wallet_ledger")
    async with tenant_txn(a) as s:
        req = await lifecycle.request_deletion(
            s, actor="op-1", plane="operator", reason_code="tenant_request", now=NOW
        )
        report = await lifecycle.dry_run(s, req, actor="op-1", now=NOW, plan=plan)
        await lifecycle.approve(s, req, actor="op-2", now=NOW)
    assert report["erase"] == {t: 3 for t in plan.erase}
    assert report["blocked"]["sources"]["rows"] == 3
    erased: dict[str, int] = {}
    reasons = []
    # LOOP(test): cap=20 passes; exit=completed or blocked
    for _ in range(20):
        async with tenant_txn(a) as s:
            out = await lifecycle.execute(s, req, actor="op-2", now=NOW, plan=plan)
        for k, v in out.erased.items():
            erased[k] = erased.get(k, 0) + v
        reasons.append(out.exit_reason)
        if out.exit_reason != "batch_cap":
            break
    assert reasons[0] == "batch_cap" and reasons[-1] == "blocked", (reasons, erased)
    assert erased == report["erase"]
    for table in plan.erase:
        assert _count(a, table) == 0, table
        assert _count(b, table) == 3, table  # another tenant is untouched
    assert _count(a, "sources") == 3  # blocked, never deleted (C23)
    assert _count(a, "wallet_ledger") == ledger_before


@pytest.mark.asyncio
async def test_deletion_needs_a_second_person_and_never_touches_ledgers_or_audit() -> None:
    t = _tenant()
    plan = lifecycle.load_plan()
    async with tenant_txn(t) as s:
        req = await lifecycle.request_deletion(
            s, actor="op-1", plane="operator", reason_code="tenant_request", now=NOW
        )
        with pytest.raises(lifecycle.LifecycleError):
            await lifecycle.approve(s, req, actor="op-2", now=NOW)  # no dry run yet
        await lifecycle.dry_run(s, req, actor="op-1", now=NOW)
        with pytest.raises(lifecycle.LifecycleError):
            await lifecycle.approve(s, req, actor="op-1", now=NOW)  # the requester
        with pytest.raises(lifecycle.LifecycleError):
            await lifecycle.execute(s, req, actor="op-1", now=NOW)  # not approved
    # the database refuses the erase without an approval, too
    with psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.scope', 'system', true)",
            (str(t),),
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
            c.execute("SELECT erase_rows(%s, 'facts', 10)", (req,))
    async with tenant_txn(t) as s:
        await lifecycle.approve(s, req, actor="op-2", now=NOW)
    with psycopg.connect(APP_DSN) as c, c.transaction():
        c.execute(
            "SELECT set_config('app.tenant_id', %s, true), set_config('app.scope', 'system', true)",
            (str(t),),
        )
        for table in ("wallet_ledger", "tool_calls", "tenant_events", "sources", "people"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege), c.transaction():
                c.execute("SELECT erase_rows(%s, %s, 10)", (req, table))
        with pytest.raises(psycopg.errors.CheckViolation), c.transaction():
            c.execute("SELECT erase_rows(%s, 'facts', 100000)", (req,))
    no_context = psycopg.connect(APP_DSN)  # no tenant context at all
    with no_context as c, c.transaction(), pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("SELECT erase_rows(%s, 'facts', 10)", (req,))
    assert not set(plan.erase) & plan.never
    for poisoned in (("audit_events",), ("wallet_ledger",), ("sources",)):
        bad = lifecycle.DeletionPlan(
            erase=plan.erase + poisoned,
            blocked=plan.blocked,
            never=plan.never,
            batch_size=1,
            max_batches_per_pass=1,
        )
        if poisoned[0] in plan.never or poisoned[0] in plan.blocked:
            with pytest.raises(lifecycle.LifecycleError):
                lifecycle.check_plan(bad)


@given(
    st.one_of(
        st.text(min_size=1, max_size=60),
        st.builds(
            lambda a, b: f"https://{a}.example/in/{b}",
            st.from_regex(r"[a-z]{1,10}", fullmatch=True),
            st.from_regex(r"[a-zA-Z0-9-]{1,20}", fullmatch=True),
        ),
    )
)
def test_normalising_a_person_is_idempotent(value: str) -> None:
    """Settings are validated again when read back: a stored canonical form must not change."""
    try:
        once = normalise_person(value)
    except SettingsError:
        return
    assert normalise_person(once) == once
