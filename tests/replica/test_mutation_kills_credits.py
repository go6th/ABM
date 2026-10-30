"""Kill tests for the mutation run (2026-10-30) of the pure and estimate functions of
``replica.credits``: the rate table, credits from dollars, the pool period, the floor, the estimate
from an account's history and the estimate of a resumed run. The session is a scripted fake that
answers by statement and records it; the rate table is a temporary file. Hermetic."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from account_intelligence import dev_limits
from account_intelligence.replica import credits as CR

ACCOUNT = UUID("00000000-0000-0000-0000-0000000000a1")
RUN = UUID("00000000-0000-0000-0000-0000000000b2")

TABLE = """
version: 1
credit_value_usd: 0.5
estimates:
  jobs:          {credits: 20,  minutes: 3}
  people:        {credits: 60,  minutes: 10}
  full:          {credits: 250, minutes: 45}
  topic:         {credits: 41,  minutes: 8}
  new_research:  {credits: 250, minutes: 480}
  file_public:   {credits: 250, minutes: 45}
history_jobs: 4
"""


@pytest.fixture
def table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Use a rate table of these numbers; the cached reading is dropped before and after."""

    def use(text: str = TABLE) -> None:
        path = tmp_path / "credits.yaml"
        path.write_text(text)
        monkeypatch.setattr(CR, "CONFIG", path)
        CR.rates.cache_clear()

    use()
    yield use
    CR.rates.cache_clear()


# ---- the rate table ---------------------------------------------------------------------------


def test_the_rate_table_is_read_and_validated(table) -> None:  # type: ignore[no-untyped-def]
    rates = CR.rates()
    assert rates.credit_value_usd == Decimal("0.5") and rates.history_jobs == 4
    assert rates.estimates["jobs"] == CR.Rate(20, 3) and rates.estimates["topic"] == CR.Rate(41, 8)
    assert CR.rates() is rates  # read once
    table(TABLE.replace("history_jobs: 4\n", ""))
    assert CR.rates().history_jobs == 3  # the default
    table(TABLE.replace("history_jobs: 4", "history_jobs: 0"))
    assert CR.rates().history_jobs == 1  # at least one job


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (("credit_value_usd: 0.5", "credit_value_usd: 0"), "credit_value_usd must be positive"),
        (("credit_value_usd: 0.5", "credit_value_usd: -1"), "credit_value_usd must be positive"),
        (
            ("  topic:         {credits: 41,  minutes: 8}\n", ""),
            "credits.yaml needs a positive estimate for topic",
        ),
        (("credits: 41,", "credits: 0,"), "credits.yaml needs a positive estimate for topic"),
        (("minutes: 8}", "minutes: 0}"), "credits.yaml needs a positive estimate for topic"),
        (
            ("  file_public:   {credits: 250, minutes: 45}\n", ""),
            "credits.yaml needs a positive estimate for file_public",
        ),
        (
            ("  new_research:  {credits: 250, minutes: 480}\n", ""),
            "credits.yaml needs a positive estimate for new_research",
        ),
    ],
)
def test_an_invalid_rate_table_fails_with_this_message(
    table, edit: tuple[str, str], message: str
) -> None:  # type: ignore[no-untyped-def]
    table(TABLE.replace(*edit))
    with pytest.raises(ValueError) as caught:
        CR.rates()
    assert str(caught.value) == message


def test_a_rate_of_exactly_one_credit_and_one_minute_is_valid(table) -> None:  # type: ignore[no-untyped-def]
    table(TABLE.replace("credits: 41,  minutes: 8", "credits: 1,  minutes: 1"))
    assert CR.rates().estimates["topic"] == CR.Rate(1, 1)


# ---- credits and the period -------------------------------------------------------------------


def test_real_spend_rounds_up_to_whole_credits(table) -> None:  # type: ignore[no-untyped-def]
    assert CR.usd_to_credits(Decimal("1.00")) == 2
    assert CR.usd_to_credits(Decimal("1.01")) == 3
    assert CR.usd_to_credits(0.5) == 1
    assert CR.usd_to_credits(Decimal("0.000001")) == 1
    assert CR.usd_to_credits(0) == 0 and CR.usd_to_credits(Decimal("-3")) == 0


def test_the_period_is_the_utc_month_whatever_the_machines_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    try:
        assert CR.period_of(datetime(2026, 10, 31, 20, tzinfo=UTC)) == "2026-10"
        assert CR.resets_on(datetime(2026, 10, 31, 20, tzinfo=UTC)) == date(2026, 11, 1)
    finally:
        monkeypatch.undo()
        time.tzset()


def test_the_period_is_the_utc_month() -> None:
    assert CR.period_of(datetime(2026, 10, 30, 12, tzinfo=UTC)) == "2026-10"
    assert CR.period_of(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-01"
    late_in_new_york = datetime(2026, 10, 31, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    assert CR.period_of(late_in_new_york) == "2026-11"


def test_the_pool_resets_on_the_first_of_the_next_utc_month() -> None:
    assert CR.resets_on(datetime(2026, 10, 30, tzinfo=UTC)) == date(2026, 11, 1)
    assert CR.resets_on(datetime(2026, 12, 31, 23, tzinfo=UTC)) == date(2027, 1, 1)
    assert CR.resets_on(datetime(2026, 1, 1, tzinfo=UTC)) == date(2026, 2, 1)
    assert CR.resets_on(
        datetime(2026, 11, 30, 23, 30, tzinfo=timezone(timedelta(hours=-5)))
    ) == date(2027, 1, 1)  # 04:30 UTC on 1 December


def test_the_floor_is_half_the_configured_rate_rounded_up(table) -> None:  # type: ignore[no-untyped-def]
    assert CR.floor_credits("jobs") == 10
    assert CR.floor_credits("topic") == 21  # 20.5 rounds up
    table(TABLE.replace("credits: 20,", "credits: 1,"))
    assert CR.floor_credits("jobs") == 1


# ---- the scripted session ---------------------------------------------------------------------


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def one(self) -> Any:
        return self.rows[0]

    def scalars(self) -> _Rows:
        return self

    def all(self) -> list[Any]:
        return self.rows


class _Session:
    def __init__(
        self,
        *,
        scalar: Any = None,
        history: list[int] | None = None,
        pool_row: Any = None,
        steps: tuple[int, int] = (0, 0),
    ) -> None:
        self._scalar, self.history, self.pool_row, self.steps = (
            scalar,
            history or [],
            pool_row,
            steps,
        )
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def scalar(self, statement: Any) -> Any:
        self.calls.append((str(statement), None))
        return self._scalar

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        sql = str(statement)
        self.calls.append((sql, params))
        if "FROM credit_entries" in sql:
            return _Rows([self.pool_row])
        if "FROM coverage_rows" in sql:
            return _Rows([SimpleNamespace(remaining=self.steps[0], planned=self.steps[1])])
        return _Rows(self.history)


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def test_the_cap_per_pull_is_the_platform_default_else_four_hundred() -> None:
    session = _Session(scalar=120)
    assert _run(CR.credit_cap_per_pull(session)) == 120
    assert session.calls == [
        ("SELECT credit_cap_per_pull FROM platform_defaults WHERE id = 1", None)
    ]
    assert _run(CR.credit_cap_per_pull(_Session(scalar=None))) == 400
    assert _run(CR.credit_cap_per_pull(_Session(scalar=0))) == 400


def test_the_pool_is_what_is_left_after_use_and_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    async def settings(session: Any) -> Any:
        seen.append(session)
        return SimpleNamespace(monthly_credit_pool=100)

    monkeypatch.setattr(CR, "client_settings", settings)
    now = datetime(2026, 10, 30, tzinfo=UTC)
    session = _Session(
        pool_row=SimpleNamespace(reserved=Decimal(50), released=Decimal(10), used=Decimal(20))
    )
    assert _run(CR.pool(session, now)) == {
        "period": "2026-10",
        "pool": 100,
        "used": 20,
        "held": 40,
        "left": 40,
        "resets_on": "2026-11-01",
    }
    sql, params = session.calls[0]
    assert sql == (
        "SELECT coalesce(sum(credits) FILTER (WHERE kind = 'reserve'), 0) AS reserved,"
        " coalesce(sum(credits) FILTER (WHERE kind = 'release'), 0) AS released,"
        " coalesce(sum(credits) FILTER (WHERE kind = 'settle'), 0) AS used"
        " FROM credit_entries WHERE period = :p"
    )
    assert params == {"p": "2026-10"} and seen == [session]
    over = _Session(pool_row=SimpleNamespace(reserved=90, released=0, used=30))
    assert _run(CR.pool(over, now))["left"] == 0  # never negative


# ---- estimates --------------------------------------------------------------------------------

PAST_SQL = (
    "SELECT credits_used FROM research_jobs WHERE account_id = :a AND scope ="
    " :s"
    " AND status = 'done' AND credits_used > 0 ORDER BY finished_at DESC LIMIT"
    " :n"
)


def _estimate(session: _Session, kind: str = "jobs", account: UUID | None = ACCOUNT) -> CR.Rate:
    return _run(CR.estimate(session, account, kind))


@pytest.fixture
def capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev_limits, "research_uncapped", lambda env=None: False)


@pytest.fixture
def uncapped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev_limits, "research_uncapped", lambda env=None: True)


def test_with_no_history_the_estimate_is_the_rate_within_the_cap(table, capped) -> None:  # type: ignore[no-untyped-def]
    session = _Session(scalar=400)
    assert _estimate(session) == CR.Rate(20, 3)
    assert session.calls[0] == (PAST_SQL, {"a": ACCOUNT, "s": "jobs", "n": 4})
    assert _estimate(_Session(scalar=5)) == CR.Rate(5, 3)  # never above the platform's cap per pull
    assert _estimate(_Session(scalar=1)) == CR.Rate(1, 3)
    assert _estimate(_Session(), "new_research") == CR.Rate(250, 480)


def test_a_kind_that_is_not_a_scope_or_an_unknown_account_never_reads_history(
    table, capped
) -> None:  # type: ignore[no-untyped-def]
    session = _Session(history=[999])
    assert _estimate(session, "new_research") == CR.Rate(250, 480)
    assert _estimate(session, "jobs", account=None) == CR.Rate(20, 3)
    assert not any("research_jobs" in sql for sql, _ in session.calls)


def test_history_averages_up_and_is_floored_at_half_the_rate_with_the_caps_on(
    table, capped
) -> None:  # type: ignore[no-untyped-def]
    assert _estimate(_Session(history=[30, 31])).credits == 31  # ceil(30.5)
    assert _estimate(_Session(history=[3, 4])).credits == 10  # the floor of the 20-credit rate
    assert _estimate(_Session(history=[300])).credits == 300  # above the rate, below the cap
    assert _estimate(_Session(history=[3000], scalar=400)).credits == 400


def test_with_the_caps_off_the_history_alone_decides(table, uncapped) -> None:  # type: ignore[no-untyped-def]
    assert _estimate(_Session(history=[3, 4])).credits == 4
    assert _estimate(_Session(history=[2])).credits == 2
    assert _estimate(_Session()).credits == 20


def test_the_steps_left_count_only_the_planned_rows(table) -> None:  # type: ignore[no-untyped-def]
    session = _Session(steps=(3, 12))
    assert _run(CR.steps_left(session, RUN)) == (3, 12)
    sql, params = session.calls[0]
    assert sql == (
        "SELECT count(*) FILTER (WHERE c.status IS DISTINCT FROM 'done') AS remaining,"
        f" count(*) AS planned FROM coverage_rows c WHERE c.run_id = :r AND {CR.PLANNED}"
    )
    assert params == {"r": RUN}


def _resume(
    steps: tuple[int, int], kind: str = "full", history: list[int] | None = None
) -> CR.Rate:
    session = _Session(steps=steps, history=history, scalar=400)
    rate = _run(CR.estimate_resume(session, ACCOUNT, kind, RUN))
    coverage = [p for sql, p in session.calls if "FROM coverage_rows" in sql]
    assert coverage in ([], [{"r": RUN}])  # the steps of THIS run are counted
    return rate


def test_a_resumed_run_reserves_the_share_of_its_steps_still_to_do(table, capped) -> None:  # type: ignore[no-untyped-def]
    assert _resume((50, 100)) == CR.Rate(125, 45)  # half of 250 credits
    assert _resume((1, 100)) == CR.Rate(125, 45)  # but never below half of the configured rate
    assert _resume((75, 100)) == CR.Rate(188, 45)  # ceil(187.5)
    assert _resume((100, 100)) == CR.Rate(250, 45)  # nothing finished: the whole estimate
    assert _resume((0, 0)) == CR.Rate(250, 45)  # no coverage rows: reserved whole
    assert _resume((5, 0)) == CR.Rate(250, 45)
    assert _resume((0, 100)) == CR.Rate(125, 45)  # everything done: the floor
    assert _resume((0, 1)) == CR.Rate(125, 45)  # a single planned step, done: the floor too
    assert _resume((1, 2)) == CR.Rate(125, 45)
    assert _resume((120, 100)) == CR.Rate(250, 45)  # more left than planned: whole


def test_a_resume_is_never_more_than_the_whole_estimate_and_at_least_one(table, capped) -> None:  # type: ignore[no-untyped-def]
    assert (
        _resume((99, 100), history=[10]).credits == 125
    )  # the floor (125) is above the history's 10
    assert _resume((50, 100), "jobs").credits == 10
    table(TABLE.replace("credits: 20,", "credits: 1,"))
    assert _resume((50, 100), "jobs").credits == 1


def test_with_the_caps_off_or_a_kind_that_is_not_a_scope_a_resume_is_the_plain_estimate(
    table, uncapped
) -> None:  # type: ignore[no-untyped-def]
    session = _Session(steps=(1, 100), scalar=400)
    assert _run(CR.estimate_resume(session, ACCOUNT, "full", RUN)) == CR.Rate(250, 45)
    assert not any("coverage_rows" in sql for sql, _ in session.calls)


def test_a_kind_that_is_not_a_scope_is_the_plain_estimate(table, capped) -> None:  # type: ignore[no-untyped-def]
    session = _Session(steps=(1, 100), scalar=400)
    assert _run(CR.estimate_resume(session, ACCOUNT, "new_research", RUN)) == CR.Rate(250, 480)
    assert not any("coverage_rows" in sql for sql, _ in session.calls)
