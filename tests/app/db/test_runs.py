"""intel.runs: lifecycle methods, foreign keys, indexes, and every CHECK constraint."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from psycopg.errors import CheckViolation, ForeignKeyViolation, NotNullViolation
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.enums import RunStatus
from app.db.models import Account, Run
from app.db.models.run import TRANSITIONS, InvalidTransition
from app.db.url import sqlalchemy_url
from app.rules import default_rules
from tests.app.db.helpers import Row, check_constraint_names, constraint_holds

T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=3)
CONFIG = default_rules().version


def _run(**kw: Any) -> Run:
    return Run(account_id=uuid.uuid4(), run_type="full", config_version=CONFIG, **kw)


# --------------------------------------------------------------------- lifecycle (no database)


def test_transition_table_covers_every_status() -> None:
    assert set(TRANSITIONS) == set(RunStatus)
    assert TRANSITIONS[RunStatus.COMPLETED] == frozenset()
    assert TRANSITIONS[RunStatus.FAILED] == frozenset()


def test_cannot_start_without_an_agreed_cap() -> None:
    r = _run()
    with pytest.raises(InvalidTransition, match="credit cap"):
        r.start(T0)
    r.agree_cap(300)
    r.start(T0)
    assert (r.status, r.started_at, r.credit_cap) == ("running", T0, 300)


def test_cap_is_agreed_only_while_queued_and_not_negative() -> None:
    r = _run()
    with pytest.raises(ValueError, match="negative"):
        r.agree_cap(-1)
    r.agree_cap(0)
    r.start(T0)
    with pytest.raises(InvalidTransition, match="before the run starts"):
        r.agree_cap(500)


def test_pause_resume_complete() -> None:
    r = _run()
    r.agree_cap(100)
    r.start(T0)
    r.pause()
    r.resume()
    assert r.started_at == T0  # resuming keeps the original start
    r.complete(T1)
    assert (r.status, r.finished_at) == ("completed", T1)


def test_fail_records_why() -> None:
    r = _run()
    with pytest.raises(ValueError, match="must say why"):
        r.fail("  ")
    r.fail(" broker returned 500 ", T1)
    assert (r.status, r.error, r.finished_at) == ("failed", "broker returned 500", T1)


@pytest.mark.parametrize(
    ("setup", "action"),
    [
        ([], "pause"),
        ([], "complete"),
        (["start", "complete"], "resume"),
        (["start", "complete"], "fail"),
        (["fail"], "resume"),
    ],
)
def test_invalid_transitions(setup: list[str], action: str) -> None:
    r = _run()
    r.agree_cap(10)
    for step in setup:
        getattr(r, step)(*(["boom"] if step == "fail" else []))
    with pytest.raises(InvalidTransition):
        getattr(r, action)(*(["boom"] if action == "fail" else []))


def test_run_type_is_validated() -> None:
    with pytest.raises(ValueError):
        Run(account_id=uuid.uuid4(), run_type="partial", config_version=CONFIG)


# --------------------------------------------------------------------- database


@pytest.fixture()
def engine(migrated_database_url: str) -> Iterator[Engine]:
    eng = create_engine(sqlalchemy_url(migrated_database_url))
    yield eng
    eng.dispose()


def _account(conn: Connection, domain: str = "acme.com") -> uuid.UUID:
    return conn.execute(
        text("INSERT INTO intel.accounts (name, domain) VALUES ('Acme', :d) RETURNING id"),
        {"d": domain},
    ).scalar_one()


def _insert(conn: Connection, **values: Any) -> uuid.UUID:
    row = {"run_type": "full", "config_version": CONFIG, **values}
    cols = ", ".join(row)
    params = ", ".join(f":{k}" for k in row)
    return conn.execute(
        text(f"INSERT INTO intel.runs ({cols}) VALUES ({params}) RETURNING id"),  # noqa: S608
        row,
    ).scalar_one()


def _violation(engine: Engine, **values: Any) -> BaseException:
    with engine.begin() as conn:
        account = values.pop("account_id", None) or _account(conn, f"{uuid.uuid4().hex}.example")
    with pytest.raises(IntegrityError) as info, engine.begin() as conn:
        _insert(conn, account_id=account, **values)
    assert info.value.orig is not None
    return info.value.orig


def test_full_lifecycle_persists_through_every_state(engine: Engine) -> None:
    with Session(engine) as s, s.begin():
        account = Account(name="Acme", domain="acme.com")
        s.add(account)
        s.flush()
        run = Run(account_id=account.id, run_type="full", config_version=CONFIG)
        s.add(run)
        s.flush()
        run_id = run.id
    for step in ("agree_cap", "start", "pause", "resume", "complete"):
        with Session(engine) as s, s.begin():
            r = s.get(Run, run_id)
            assert r is not None
            if step == "agree_cap":
                r.agree_cap(250)
            else:
                getattr(r, step)()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status, credit_cap, started_at, finished_at FROM intel.runs")
        ).one()
    assert row.status == "completed"
    assert row.credit_cap == 250
    assert row.finished_at >= row.started_at


def test_raw_insert_defaults_to_queued(engine: Engine) -> None:
    with engine.begin() as conn:
        rid = _insert(conn, account_id=_account(conn))
        assert (
            conn.execute(
                text("SELECT status FROM intel.runs WHERE id = :i"), {"i": rid}
            ).scalar_one()
            == "queued"
        )


def test_refresh_links_to_a_parent_of_the_same_account(engine: Engine) -> None:
    with engine.begin() as conn:
        acme = _account(conn)
        parent = _insert(conn, account_id=acme)
        _insert(conn, account_id=acme, run_type="refresh", parent_run_id=parent)
        other = _account(conn, "other.example")
    with pytest.raises(IntegrityError) as info, engine.begin() as conn:
        _insert(conn, account_id=other, run_type="refresh", parent_run_id=parent)
    assert isinstance(info.value.orig, ForeignKeyViolation)
    assert info.value.orig.diag.constraint_name == "fk_runs_parent_run_id_account_id_runs"


def test_account_must_exist(engine: Engine) -> None:
    err = _violation(engine, account_id=uuid.uuid4())
    assert isinstance(err, ForeignKeyViolation)
    assert err.diag.constraint_name == "fk_runs_account_id_accounts"


def test_accounts_and_parent_runs_cannot_be_deleted_while_referenced(engine: Engine) -> None:
    with engine.begin() as conn:
        acme = _account(conn)
        parent = _insert(conn, account_id=acme)
        _insert(conn, account_id=acme, run_type="refresh", parent_run_id=parent)
    for statement, params in [
        ("DELETE FROM intel.accounts WHERE id = :i", {"i": acme}),
        ("DELETE FROM intel.runs WHERE id = :i", {"i": parent}),
    ]:
        with pytest.raises(IntegrityError) as info, engine.begin() as conn:
            conn.execute(text(statement), params)
        assert isinstance(info.value.orig, ForeignKeyViolation)


@pytest.mark.parametrize("column", ["run_type", "config_version"])
def test_required_columns(engine: Engine, column: str) -> None:
    assert isinstance(_violation(engine, **{column: None}), NotNullViolation)


def _status(status: str, **extra: tuple[Any, str]) -> Row:
    return {"status": (status, "text"), **extra}


def _times(status: str, started: datetime | None, finished: datetime | None) -> Row:
    return _status(
        status, started_at=(started, "timestamptz"), finished_at=(finished, "timestamptz")
    )


RID = uuid.uuid4()
CHECKS: list[tuple[str, list[Row], list[Row]]] = [
    (
        "ck_runs_cap_agreed_before_start",
        [
            _status("queued", credit_cap=(None, "int")),
            _status("queued", credit_cap=(100, "int")),
            _status("running", credit_cap=(100, "int")),
        ],
        [
            _status("running", credit_cap=(None, "int")),
            _status("completed", credit_cap=(None, "int")),
            _status("failed", credit_cap=(None, "int")),
        ],
    ),
    (
        "ck_runs_credit_cap_not_negative",
        [{"credit_cap": (0, "int")}],
        [{"credit_cap": (-1, "int")}],
    ),
    (
        "ck_runs_finished_after_started",
        [_times("x", T0, T1), _times("x", T0, T0), _times("x", None, T1)],
        [_times("x", T1, T0)],
    ),
    (
        "ck_runs_config_version_not_blank",
        [{"config_version": (CONFIG, "text")}],
        [{"config_version": (" ", "text")}],
    ),
    (
        "ck_runs_refresh_has_parent",
        [
            {"run_type": ("full", "text"), "parent_run_id": (None, "uuid")},
            {"run_type": ("refresh", "text"), "parent_run_id": (uuid.uuid4(), "uuid")},
        ],
        [
            {"run_type": ("refresh", "text"), "parent_run_id": (None, "uuid")},
            {"run_type": ("full", "text"), "parent_run_id": (uuid.uuid4(), "uuid")},
        ],
    ),
    (
        "ck_runs_not_own_parent",
        [{"id": (RID, "uuid"), "parent_run_id": (uuid.uuid4(), "uuid")}],
        [{"id": (RID, "uuid"), "parent_run_id": (RID, "uuid")}],
    ),
    (
        "ck_runs_timestamps_match_status",
        [
            _times("queued", None, None),
            _times("running", T0, None),
            _times("paused", T0, None),
            _times("completed", T0, T1),
            _times("failed", None, T1),
            _times("failed", T0, T1),
        ],
        [
            _times("queued", T0, None),
            _times("queued", None, T1),
            _times("running", None, None),
            _times("running", T0, T1),
            _times("paused", T0, T1),
            _times("completed", T0, None),
            _times("completed", None, T1),
            _times("failed", T0, None),
        ],
    ),
    (
        "ck_runs_error_iff_failed",
        [_status("failed", error=("boom", "text")), _status("running", error=(None, "text"))],
        [_status("failed", error=(None, "text")), _status("completed", error=("boom", "text"))],
    ),
    (
        "ck_runs_error_not_blank",
        [{"error": ("boom", "text")}, {"error": (None, "text")}],
        [{"error": ("  ", "text")}],
    ),
    ("ck_runs_run_type", [{"run_type": ("refresh", "text")}], [{"run_type": ("Full", "text")}]),
    ("ck_runs_run_status", [{"status": ("paused", "text")}], [{"status": ("cancelled", "text")}]),
]


@pytest.mark.parametrize(("name", "good", "bad"), CHECKS, ids=[c[0] for c in CHECKS])
def test_each_check_constraint(engine: Engine, name: str, good: list[Row], bad: list[Row]) -> None:
    with engine.connect() as conn:
        for row in good:
            assert constraint_holds(conn, name, row), f"{name} rejected {row}"
        for row in bad:
            assert not constraint_holds(conn, name, row), f"{name} accepted {row}"


def test_every_check_constraint_is_tested(engine: Engine) -> None:
    with engine.connect() as conn:
        assert check_constraint_names(conn, "intel.runs") == {c[0] for c in CHECKS}


@pytest.mark.parametrize(
    "values",
    [
        {"status": "running", "started_at": T0},  # no cap
        {"status": "running", "credit_cap": 10},  # no started_at
        {"status": "completed", "credit_cap": 10, "started_at": T1, "finished_at": T0},
        {"status": "failed", "credit_cap": 10, "finished_at": T1},  # no error
        {"credit_cap": -5},
        {"run_type": "refresh"},  # refresh without parent
        {"config_version": ""},
        {"status": "done"},
    ],
)
def test_bad_rows_are_rejected_by_the_database(engine: Engine, values: dict[str, Any]) -> None:
    assert isinstance(_violation(engine, **values), CheckViolation)


def test_indexes(engine: Engine) -> None:
    with engine.connect() as conn:
        defs = dict(
            conn.execute(
                text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'runs'")
            ).all()
        )
    assert "(account_id, created_at DESC)" in defs["ix_runs_account_id_created_at"]
    assert "(status)" in defs["ix_runs_status"]
