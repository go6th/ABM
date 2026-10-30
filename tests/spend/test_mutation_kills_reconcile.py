"""Kill tests for ``spend.reconcile.reconcile_run`` (mutation run 2026-10-30). The run is read
through a scripted session that answers each of the five statements by its text and records the
one write, so every branch of the reading and the statements themselves are pinned without a
database. Money is in dollars as the tables hold it, in micro-dollars as the code computes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from spend import reconcile as R
from spend.rollup import EventRow

RUN = UUID("00000000-0000-0000-0000-0000000000a1")
NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)

TIMES_SQL = (
    "SELECT args_hash, attempt, created_at FROM tool_call_events WHERE run_id = :r"
    " AND event = 'started'"
)
RUN_SQL = "SELECT spent_usd, cap_version, budget_usd FROM account_runs WHERE id = :r"
APPROVED_SQL = (
    "SELECT v.version, v.cap_usd FROM run_cap_versions v JOIN run_cap_events e"
    " ON e.run_id = v.run_id AND e.version = v.version AND e.event = 'approved'"
    " WHERE v.run_id = :r ORDER BY v.version DESC LIMIT 1"
)
CONNECTOR_SQL = (
    "SELECT COALESCE(SUM(e.charged_usd) FILTER (WHERE e.event IN ('done', 'failed',"
    " 'abandoned')), 0) + COALESCE(SUM(e.reserved_usd) FILTER (WHERE e.event ="
    " 'started' AND NOT EXISTS (SELECT 1 FROM tool_call_events t WHERE t.run_id ="
    " e.run_id AND t.args_hash = e.args_hash AND t.attempt = e.attempt AND t.event IN"
    " ('done', 'failed', 'abandoned'))), 0) FROM tool_call_events e WHERE e.run_id = :r"
)
UPDATE_SQL = (
    "UPDATE account_runs SET unreconciled_usd = :u WHERE id = :r AND unreconciled_usd <> :u"
)


class _Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def all(self) -> Any:
        return self.value

    def first(self) -> Any:
        return self.value

    def scalar_one(self) -> Any:
        return self.value


class _Session:
    def __init__(
        self,
        *,
        times: list[Any] | None = None,
        run: Any = None,
        approved: Any = None,
        connector: Any = Decimal(0),
    ) -> None:
        self.answers = {
            TIMES_SQL: times or [],
            RUN_SQL: run,
            APPROVED_SQL: approved,
            CONNECTOR_SQL: connector,
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        sql = str(statement)
        self.calls.append((sql, params))
        return _Result(self.answers.get(sql))


def _event(
    h: str,
    event: str,
    *,
    reserved: int = 0,
    charged: int = 0,
    attempt: int = 1,
    provider: str = "p1",
    dup: bool = False,
) -> EventRow:
    return EventRow(
        provider_code=provider,
        operation="op",
        route=None,
        args_hash=h,
        attempt=attempt,
        event=event,
        reserved_micros=reserved,
        charged_micros=charged,
        credits=None,
        cost_source=None,
        possible_duplicate=dup,
    )


def _reconcile(
    session: _Session, rows: list[EventRow], monkeypatch: pytest.MonkeyPatch, ttl: int = 600
) -> R.Reconciliation:
    import asyncio

    async def fake_events(s: Any, run_id: UUID) -> list[EventRow]:
        assert s is session and run_id == RUN
        return rows

    monkeypatch.setattr(R, "ledger_events", fake_events)
    return asyncio.run(R.reconcile_run(session, RUN, NOW, ttl))  # type: ignore[arg-type]


def _run(spent: str, version: int | None = None, budget: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        spent_usd=Decimal(spent),
        cap_version=version,
        budget_usd=Decimal(budget) if budget is not None else None,
    )


ROWS = [_event("a", "done", charged=1_500_000), _event("b", "started", reserved=250_000)]


def test_a_consistent_run_has_no_problem_and_records_the_stale_amount() -> None:
    mp = pytest.MonkeyPatch()
    try:
        session = _Session(
            times=[
                SimpleNamespace(args_hash="b", attempt=1, created_at=NOW - timedelta(seconds=700))
            ],
            run=_run("1.75", 3, "5"),
            approved=SimpleNamespace(version=3, cap_usd=Decimal("5")),
            connector=Decimal("1.75"),
        )
        out = _reconcile(session, ROWS, mp)
    finally:
        mp.undo()
    assert out.problems == () and out.ok
    assert out.ledger_micros == 1_750_000
    assert (out.stale_attempts, out.stale_micros) == (1, 250_000)
    assert [(p.key, p.spent_micros) for p in out.by_provider] == [("p1", 1_750_000)]
    statements = [c[0] for c in session.calls]
    assert statements == [TIMES_SQL, RUN_SQL, APPROVED_SQL, CONNECTOR_SQL, UPDATE_SQL]
    assert [c[1] for c in session.calls] == [
        {"r": RUN},
        {"r": RUN},
        {"r": RUN},
        {"r": RUN},
        {"u": Decimal("0.25"), "r": RUN},
    ]


def test_a_fresh_open_attempt_is_not_stale_and_records_zero() -> None:
    mp = pytest.MonkeyPatch()
    try:
        session = _Session(
            times=[
                SimpleNamespace(args_hash="b", attempt=1, created_at=NOW - timedelta(seconds=599))
            ],
            run=_run("1.75"),
            connector=Decimal("1.75"),
        )
        out = _reconcile(session, ROWS, mp)
    finally:
        mp.undo()
    assert (out.stale_attempts, out.stale_micros) == (0, 0)
    assert session.calls[-1] == (UPDATE_SQL, {"u": Decimal("0"), "r": RUN})
    # the lease passed in is the one used: with a 60 s lease the same attempt is stale
    mp2 = pytest.MonkeyPatch()
    try:
        out2 = _reconcile(session, ROWS, mp2, ttl=60)
    finally:
        mp2.undo()
    assert out2.stale_attempts == 1


def test_a_spent_total_that_differs_from_the_ledger_is_a_problem() -> None:
    mp = pytest.MonkeyPatch()
    try:
        out = _reconcile(_Session(run=_run("2"), connector=Decimal("1.75")), ROWS, mp)
    finally:
        mp.undo()
    assert out.problems == ("spent 2000000 != ledger 1750000",)
    assert not out.ok


def test_a_run_row_that_is_missing_counts_as_nothing_spent_and_no_cap() -> None:
    mp = pytest.MonkeyPatch()
    try:
        out = _reconcile(
            _Session(
                run=None,
                approved=SimpleNamespace(version=1, cap_usd=Decimal("5")),
                connector=Decimal("1.75"),
            ),
            ROWS,
            mp,
        )
    finally:
        mp.undo()
    assert out.problems == ("spent 0 != ledger 1750000",)  # no cap comparison without a run row


def test_the_cap_columns_of_the_run_must_equal_the_latest_approved_version() -> None:
    approved = SimpleNamespace(version=3, cap_usd=Decimal("5"))
    cases = [
        (_run("1.75", 3, "5"), approved, ()),
        (_run("1.75", 3, "6"), approved, ("run cap (3, 6000000) != latest approved (3, 5000000)",)),
        (_run("1.75", 2, "5"), approved, ("run cap (2, 5000000) != latest approved (3, 5000000)",)),
        (_run("1.75", 3, None), approved, ("run cap (3, None) != latest approved (3, 5000000)",)),
        (_run("1.75", None, "6"), approved, ()),  # the run has no cap version: not compared
        (_run("1.75", 3, "6"), None, ()),  # nothing approved yet: not compared
    ]
    for run, appr, expected in cases:
        mp = pytest.MonkeyPatch()
        try:
            out = _reconcile(_Session(run=run, approved=appr, connector=Decimal("1.75")), ROWS, mp)
        finally:
            mp.undo()
        assert out.problems == expected


def test_a_connector_sum_that_differs_from_the_rollup_is_a_problem() -> None:
    mp = pytest.MonkeyPatch()
    try:
        out = _reconcile(_Session(run=_run("1.75"), connector=Decimal("1.7")), ROWS, mp)
    finally:
        mp.undo()
    assert out.problems == ("rollup differs from the connector ledger sum",)


def test_problems_are_listed_in_the_order_found() -> None:
    mp = pytest.MonkeyPatch()
    try:
        out = _reconcile(
            _Session(
                run=_run("9", 1, "6"),
                approved=SimpleNamespace(version=1, cap_usd=Decimal("5")),
                connector=Decimal("0"),
            ),
            ROWS,
            mp,
        )
    finally:
        mp.undo()
    assert out.problems == (
        "spent 9000000 != ledger 1750000",
        "run cap (1, 6000000) != latest approved (1, 5000000)",
        "rollup differs from the connector ledger sum",
    )


def test_the_start_time_of_an_attempt_is_keyed_by_hash_and_attempt() -> None:
    rows = [
        _event("a", "started", reserved=100_000, attempt=2),
        _event("a", "started", reserved=200_000, attempt=1),
    ]
    times = [
        SimpleNamespace(args_hash="a", attempt=1, created_at=NOW - timedelta(seconds=10)),
        SimpleNamespace(args_hash="a", attempt=2, created_at=NOW - timedelta(seconds=1000)),
    ]
    mp = pytest.MonkeyPatch()
    try:
        out = _reconcile(_Session(times=times, run=_run("0.3"), connector=Decimal("0.3")), rows, mp)
    finally:
        mp.undo()
    assert (out.stale_attempts, out.stale_micros) == (1, 100_000)
