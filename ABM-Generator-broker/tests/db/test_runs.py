"""Runs table rules (data layer v1, step 8).

Constraint tests use the admin connection so the database itself is shown to
reject bad rows; RLS and grant tests use the app_rw session.
"""

from __future__ import annotations

from itertools import product

import psycopg
import pytest
from sqlalchemy import select, text

from account_intelligence.db.enums import RUN_TRANSITIONS, RunStatus
from account_intelligence.db.models import Run
from tests.db.conftest import make_account, make_run, make_tenant, set_tenant

STATUSES = [status.value for status in RunStatus]


@pytest.fixture
def account(admin):  # type: ignore[no-untyped-def]
    tenant = make_tenant(admin)
    return tenant, make_account(admin, tenant)


def test_queued_run_needs_no_credit_cap(admin, account) -> None:  # type: ignore[no-untyped-def]
    make_run(admin, *account, "queued", credit_cap=None)


@pytest.mark.parametrize("status", ["running", "paused", "completed", "failed"])
def test_credit_cap_is_required_once_a_run_leaves_queued(admin, account, status) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_runs_credit_cap_before_start"):
        make_run(admin, *account, status, credit_cap=None)


def test_credit_cap_cannot_be_dropped_after_start(admin, account) -> None:  # type: ignore[no-untyped-def]
    run = make_run(admin, *account, "running")
    with pytest.raises(psycopg.errors.CheckViolation, match="credit_cap_before_start"):
        admin.execute("UPDATE runs SET credit_cap = NULL WHERE id = %s", (run,))


def test_credit_cap_must_be_positive(admin, account) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_runs_credit_cap_positive"):
        make_run(admin, *account, credit_cap=0)


def test_finished_at_cannot_precede_started_at(admin, account) -> None:  # type: ignore[no-untyped-def]
    run = make_run(admin, *account, "running")
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_runs_finished_after_started"):
        admin.execute(
            "UPDATE runs SET status = 'completed', finished_at = started_at - interval '1 second'"
            " WHERE id = %s",
            (run,),
        )


@pytest.mark.parametrize(
    ("status", "started", "finished"),
    [
        ("queued", "now()", "NULL"),
        ("running", "NULL", "NULL"),
        ("running", "now()", "now()"),
        ("completed", "now()", "NULL"),
    ],
)
def test_timestamps_must_match_the_status(admin, account, status, started, finished) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_runs_lifecycle_timestamps"):
        admin.execute(
            "INSERT INTO runs (tenant_id, account_id, run_type, status, config_version,"
            f" credit_cap, started_at, finished_at) VALUES (%s, %s, 'full', %s, 'cfg', 10,"
            f" {started}, {finished})",
            (*account, status),
        )


def test_a_failed_run_must_say_why_and_only_a_failed_run_has_an_error(admin, account) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_runs_error_only_when_failed"):
        admin.execute(
            "INSERT INTO runs (tenant_id, account_id, run_type, status, config_version,"
            " credit_cap, finished_at) VALUES (%s, %s, 'full', 'failed', 'cfg', 10, now())",
            account,
        )
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_runs_error_only_when_failed"):
        admin.execute(
            "INSERT INTO runs (tenant_id, account_id, run_type, config_version, error)"
            " VALUES (%s, %s, 'full', 'cfg', 'not failed')",
            account,
        )


def test_config_version_cannot_be_blank(admin, account) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(psycopg.errors.CheckViolation, match="config_version_not_blank"):
        admin.execute(
            "INSERT INTO runs (tenant_id, account_id, run_type, config_version)"
            " VALUES (%s, %s, 'full', '  ')",
            account,
        )


def test_refresh_requires_a_parent_and_full_run_has_none(admin, account) -> None:  # type: ignore[no-untyped-def]
    parent = make_run(admin, *account, "completed")
    make_run(admin, *account, run_type="refresh", parent=parent)
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_runs_refresh_has_parent"):
        make_run(admin, *account, run_type="refresh", parent=None)
    with pytest.raises(psycopg.errors.CheckViolation, match="ck_runs_refresh_has_parent"):
        make_run(admin, *account, run_type="full", parent=parent)


def test_refresh_parent_must_belong_to_the_same_account(admin, account) -> None:  # type: ignore[no-untyped-def]
    tenant, _ = account
    other_account = make_account(admin, tenant)
    parent = make_run(admin, tenant, other_account, "completed")
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="parent_run_id_runs"):
        make_run(admin, *account, run_type="refresh", parent=parent)


def test_run_cannot_reference_another_tenants_account(admin) -> None:  # type: ignore[no-untyped-def]
    mine, theirs = make_tenant(admin), make_tenant(admin)
    their_account = make_account(admin, theirs)
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="account_id_accounts"):
        make_run(admin, mine, their_account)


@pytest.mark.parametrize(("old", "new"), list(product(STATUSES, STATUSES)))
def test_status_transitions_match_run_transitions(admin, account, old, new) -> None:  # type: ignore[no-untyped-def]
    """Every (from, to) pair: the trigger allows exactly RUN_TRANSITIONS."""
    run = make_run(admin, *account, old)
    update = (
        "UPDATE runs SET status = %(new)s,"
        " started_at = CASE WHEN %(new)s IN ('running', 'paused', 'completed')"
        "   THEN coalesce(started_at, now()) ELSE started_at END,"
        " finished_at = CASE WHEN %(new)s IN ('completed', 'failed')"
        "   THEN coalesce(finished_at, now()) ELSE NULL END,"
        " error = CASE WHEN %(new)s = 'failed' THEN coalesce(error, 'failed') END"
        " WHERE id = %(id)s"
    )
    allowed = new == old or RunStatus(new) in RUN_TRANSITIONS[RunStatus(old)]
    if allowed:
        admin.execute(update, {"new": new, "id": run})
    else:
        with pytest.raises(psycopg.errors.CheckViolation, match="status cannot change"):
            admin.execute(update, {"new": new, "id": run})


@pytest.mark.parametrize("assignment", ["run_type = 'refresh'", "account_id = gen_random_uuid()"])
def test_identity_columns_are_immutable(admin, account, assignment) -> None:  # type: ignore[no-untyped-def]
    run = make_run(admin, *account)
    with pytest.raises(psycopg.errors.CheckViolation, match="immutable"):
        admin.execute(f"UPDATE runs SET {assignment} WHERE id = %s", (run,))


def test_runtime_role_cannot_delete_runs(db, admin, account) -> None:  # type: ignore[no-untyped-def]
    tenant, account_id = account
    run = make_run(admin, tenant, account_id)
    set_tenant(db, tenant)
    with pytest.raises(Exception, match="permission denied"):
        db.execute(text("DELETE FROM runs WHERE id = :id"), {"id": run})


def test_orm_roundtrip_takes_tenant_from_context(db, admin, account) -> None:  # type: ignore[no-untyped-def]
    tenant, account_id = account
    set_tenant(db, tenant)
    run = Run(account_id=account_id, run_type="full", config_version="cfg-2026-10")
    db.add(run)
    db.flush()
    db.refresh(run)
    assert run.tenant_id == tenant
    assert run.status == "queued"
    assert db.execute(select(Run).where(Run.id == run.id)).scalar_one() is run


def test_runs_are_invisible_to_other_tenants(db, admin, account) -> None:  # type: ignore[no-untyped-def]
    tenant, account_id = account
    make_run(admin, tenant, account_id)
    set_tenant(db, make_tenant(admin))
    assert db.execute(select(Run).where(Run.account_id == account_id)).first() is None


def test_query_indexes_exist(admin) -> None:  # type: ignore[no-untyped-def]
    rows = admin.execute("SELECT indexdef FROM pg_indexes WHERE tablename = 'runs'").fetchall()
    defs = " ".join(row[0] for row in rows)
    assert (
        "ix_runs_account_id_created_at ON public.runs USING btree (account_id, created_at DESC)"
        in defs
    )
    assert "ix_runs_status" in defs
