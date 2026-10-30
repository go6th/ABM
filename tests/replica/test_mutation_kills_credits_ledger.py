"""Kill tests for the ledger functions of ``replica.credits`` (mutation run 2026-10-30): reserve,
settle, promote, claim, settle a run and the nightly ledger check, on a scripted session that
answers each statement by its exact text (``credits_sql``) and records the order and the
parameters. The behaviour asserted is the credit rules of the module docstring: a confirm never
spends credits the pool does not hold, a settlement never charges more than was reserved, every
reserve is released. Hermetic: no database."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from account_intelligence import dev_limits
from account_intelligence.replica import credits as CR
from tests.replica import credits_sql as Q

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)
ACCOUNT = UUID("00000000-0000-0000-0000-0000000000a1")
JOB = UUID("00000000-0000-0000-0000-0000000000b2")
OTHER_JOB = UUID("00000000-0000-0000-0000-0000000000b3")
RUN = UUID("00000000-0000-0000-0000-0000000000c3")
REQUEST = UUID("00000000-0000-0000-0000-0000000000d4")
TURN = UUID("00000000-0000-0000-0000-0000000000e5")
RESUME = UUID("00000000-0000-0000-0000-0000000000f6")


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def first(self) -> Any:
        return self.rows[0] if self.rows else None

    def one(self) -> Any:
        return self.rows[0]

    def all(self) -> list[Any]:
        return self.rows

    def scalar_one(self) -> Any:
        return self.rows[0]


class _Session:
    """Answers a statement by its exact text; any other statement is a test failure."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def _answer(self, statement: Any, params: dict[str, Any] | None) -> Any:
        sql = str(statement)
        self.calls.append((sql, params))
        assert sql in self.answers, f"unexpected statement: {sql}"
        value = self.answers[sql]
        return value(params) if callable(value) else value

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Rows:
        return _Rows(self._answer(statement, params))

    async def scalar(self, statement: Any, params: dict[str, Any] | None = None) -> Any:
        return self._answer(statement, params)

    def statements(self) -> list[str]:
        return [sql for sql, _ in self.calls]

    def params_of(self, sql: str) -> list[dict[str, Any] | None]:
        return [p for s, p in self.calls if s == sql]


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


RATES = """
version: 1
credit_value_usd: 0.5
estimates:
  jobs:          {credits: 20,  minutes: 3}
  people:        {credits: 60,  minutes: 10}
  full:          {credits: 250, minutes: 45}
  topic:         {credits: 40,  minutes: 8}
  new_research:  {credits: 250, minutes: 480}
  file_public:   {credits: 250, minutes: 45}
"""


@pytest.fixture(autouse=True)
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    path = tmp_path / "credits.yaml"
    path.write_text(RATES)
    monkeypatch.setattr(CR, "CONFIG", path)
    CR.rates.cache_clear()
    state = SimpleNamespace(pool=100, settings_calls=0, uncapped=False, sessions=[])

    async def settings(session: Any) -> Any:
        state.settings_calls += 1
        state.sessions.append(session)
        return SimpleNamespace(monthly_credit_pool=state.pool)

    monkeypatch.setattr(CR, "client_settings", settings)
    monkeypatch.setattr(dev_limits, "research_uncapped", lambda env=None: state.uncapped)
    yield state
    CR.rates.cache_clear()


def _pool_row(reserved: int = 0, released: int = 0, used: int = 0) -> list[Any]:
    return [SimpleNamespace(reserved=reserved, released=released, used=used)]


# ---- has_request and open_job -----------------------------------------------------------------


def test_a_repeated_request_is_found_by_its_id() -> None:
    session = _Session({Q.HAS_REQUEST: 1})
    assert _run(CR.has_request(session, REQUEST)) is True
    assert session.calls == [(Q.HAS_REQUEST, {"r": REQUEST})]
    assert _run(CR.has_request(_Session({Q.HAS_REQUEST: None}), REQUEST)) is False


def test_the_open_job_of_an_account_is_the_newest_waiting_queued_or_running() -> None:
    row = SimpleNamespace(id=JOB)
    session = _Session({Q.OPEN_JOB: [row]})
    assert _run(CR.open_job(session, ACCOUNT)) is row
    assert session.calls == [(Q.OPEN_JOB, {"a": ACCOUNT})]
    assert _run(CR.open_job(_Session({Q.OPEN_JOB: []}), ACCOUNT)) is None


# ---- reserve ----------------------------------------------------------------------------------


def _reserve_session(
    *, prior: Any = None, open_job: Any = None, pool: list[Any] | None = None, job_id: Any = JOB
) -> _Session:
    return _Session(
        {
            Q.LOCK: [],
            Q.PRIOR: [prior] if prior is not None else [],
            Q.OPEN_JOB: [open_job] if open_job is not None else [],
            Q.POOL: pool if pool is not None else _pool_row(),
            Q.INSERT_JOB: [job_id],
            Q.ENTRY: [],
        }
    )


def _reserve(session: _Session, **kw: Any) -> tuple[UUID, str]:
    args: dict[str, Any] = dict(
        account_id=ACCOUNT,
        kind="web_refresh",
        scope="full",
        credits=30,
        actor="u1",
        now=NOW,
    )
    return _run(CR.reserve(session, **{**args, **kw}))


def test_a_confirm_creates_the_job_and_reserves_its_credits(world: Any) -> None:
    session = _reserve_session()
    assert _reserve(
        session,
        kind="web_refresh",
        scope="jobs",
        request_id=REQUEST,
        from_turn_id=TURN,
        topic="t",
        models={"a": 1},
        resume_from=RESUME,
        goal="g",
    ) == (JOB, "queued")
    assert session.statements() == [Q.LOCK, Q.PRIOR, Q.OPEN_JOB, Q.POOL, Q.INSERT_JOB, Q.ENTRY]
    assert (
        world.settings_calls == 2
    )  # the settings row is created before the lock and read by the pool
    assert world.sessions == [session, session]
    assert session.params_of(Q.PRIOR) == [{"r": REQUEST}]
    assert session.params_of(Q.OPEN_JOB) == [{"a": ACCOUNT}]
    assert session.params_of(Q.INSERT_JOB) == [
        {
            "a": ACCOUNT,
            "k": "web_refresh",
            "s": "jobs",
            "st": "queued",
            "p": "2026-10",
            "c": 30,
            "by": "u1",
            "r": REQUEST,
            "t": TURN,
            "topic": "t",
            "now": NOW,
            "models": '{"a": 1}',
            "resume": RESUME,
            "goal": "g",
        }
    ]
    assert session.params_of(Q.ENTRY) == [
        {
            "p": "2026-10",
            "k": "reserve",
            "c": 30,
            "j": JOB,
            "pu": "web_refresh",
            "a": "u1",
            "now": NOW,
        }
    ]


def test_no_request_id_skips_the_repeat_check_and_empty_models_are_null() -> None:
    session = _reserve_session()
    _reserve(session, kind="new_research", models={})
    assert Q.PRIOR not in session.statements()
    params = session.params_of(Q.INSERT_JOB)[0]
    assert params is not None and params["models"] is None and params["r"] is None
    assert params["topic"] is None and params["resume"] is None and params["goal"] is None
    assert session.params_of(Q.ENTRY)[0]["pu"] == "new_research"  # type: ignore[index]


def test_a_repeated_request_returns_the_first_job_and_changes_nothing() -> None:
    prior = SimpleNamespace(id=str(OTHER_JOB), status="waiting_credit_limit")
    session = _reserve_session(prior=prior)
    assert _reserve(session, request_id=REQUEST) == (OTHER_JOB, "waiting_credit_limit")
    assert session.statements() == [Q.LOCK, Q.PRIOR]


def test_an_account_with_an_open_job_is_refused_unless_the_caller_allows_more() -> None:
    session = _reserve_session(open_job=SimpleNamespace(id=OTHER_JOB))
    with pytest.raises(CR.JobOpen):
        _reserve(session)
    assert Q.INSERT_JOB not in session.statements()
    allowed = _reserve_session(open_job=SimpleNamespace(id=OTHER_JOB))
    assert _reserve(allowed, one_open_per_account=False) == (JOB, "queued")
    assert Q.OPEN_JOB not in allowed.statements()


def test_the_pool_must_cover_the_credits_or_the_confirm_is_refused() -> None:
    session = _reserve_session(pool=_pool_row(reserved=80))  # 100 - 80 = 20 left
    with pytest.raises(CR.CreditsShort) as short:
        _reserve(session, credits=21)
    assert (short.value.needed, short.value.left) == (21, 20)
    assert Q.INSERT_JOB not in session.statements()
    assert _reserve(_reserve_session(pool=_pool_row(reserved=80)), credits=20) == (JOB, "queued")


def test_a_scheduled_job_waits_for_credits_without_reserving_them() -> None:
    session = _reserve_session(pool=_pool_row(reserved=80))
    assert _reserve(session, credits=21, wait_if_short=True) == (JOB, "waiting_credit_limit")
    assert session.params_of(Q.INSERT_JOB)[0]["st"] == "waiting_credit_limit"  # type: ignore[index]
    assert Q.ENTRY not in session.statements()


def test_the_pool_is_not_a_gate_when_it_is_switched_off_by_the_caller_or_the_caps(
    world: Any,
) -> None:
    open_gate = _reserve_session(pool=_pool_row(reserved=100))
    assert _reserve(open_gate, enforce_pool=False, credits=50) == (JOB, "queued")
    assert Q.ENTRY in open_gate.statements()
    world.uncapped = True
    caps_off = _reserve_session(pool=_pool_row(reserved=100))
    assert _reserve(caps_off, credits=50) == (JOB, "queued")
    assert Q.ENTRY in caps_off.statements()


# ---- settle -----------------------------------------------------------------------------------


def _job(status: str = "running", credits: int = 20, **kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = dict(
        id=JOB,
        kind="web_refresh",
        period="2026-10",
        credits_estimated=credits,
        status=status,
        requested_by="u1",
    )
    return SimpleNamespace(**{**base, **kw})


def _settle_session(job: Any) -> _Session:
    return _Session({Q.SELECT_JOB: [job] if job is not None else [], Q.ENTRY: [], Q.UPDATE_JOB: []})


def _settle(session: _Session, spent: str, **kw: Any) -> int:
    return _run(CR.settle(session, JOB, spent_usd=Decimal(spent), now=NOW, **kw))


def test_a_job_that_is_missing_or_ended_is_left_as_it_is() -> None:
    for job in (None, _job("done"), _job("failed"), _job("canceled")):
        session = _settle_session(job)
        assert _settle(session, "5") == 0
        assert session.statements() == [Q.SELECT_JOB]
    session = _settle_session(None)
    _settle(session, "5")
    assert session.params_of(Q.SELECT_JOB) == [{"j": JOB}]


def test_a_settlement_charges_what_was_used_and_releases_the_whole_reserve() -> None:
    session = _settle_session(_job("running", credits=20))
    assert _settle(session, "3.00", reason=None, stop_reason=None) == 6  # 3.00 / 0.50
    assert session.statements() == [Q.SELECT_JOB, Q.ENTRY, Q.ENTRY, Q.UPDATE_JOB]
    settle_entry, release_entry = session.params_of(Q.ENTRY)
    assert settle_entry == {
        "p": "2026-10",
        "k": "settle",
        "c": 6,
        "j": JOB,
        "pu": "web_refresh",
        "a": "system",
        "now": NOW,
    }
    assert release_entry == {
        "p": "2026-10",
        "k": "release",
        "c": 20,
        "j": JOB,
        "pu": "web_refresh",
        "a": "system",
        "now": NOW,
    }
    assert session.params_of(Q.UPDATE_JOB) == [
        {
            "st": "done",
            "u": 6,
            "now": NOW,
            "r": None,
            "sr": None,
            "j": JOB,
        }
    ]


def test_a_settlement_never_charges_more_than_was_reserved() -> None:
    session = _settle_session(_job("queued", credits=10))
    assert (
        _settle(session, "100", status="failed", reason="boom", stop_reason="credit_cap_reached")
        == 10
    )
    assert session.params_of(Q.ENTRY)[0]["c"] == 10  # type: ignore[index]
    assert session.params_of(Q.UPDATE_JOB) == [
        {
            "st": "failed",
            "u": 10,
            "now": NOW,
            "r": "boom",
            "sr": "credit_cap_reached",
            "j": JOB,
        }
    ]


def test_a_job_that_used_nothing_only_releases() -> None:
    session = _settle_session(_job("running", credits=20))
    assert _settle(session, "0") == 0
    assert [p["k"] for p in session.params_of(Q.ENTRY)] == ["release"]  # type: ignore[index]


def test_a_job_that_held_nothing_moves_no_credits() -> None:
    session = _settle_session(_job("waiting_credit_limit", credits=20))
    assert _settle(session, "50") == 0
    assert session.statements() == [Q.SELECT_JOB, Q.UPDATE_JOB]


# ---- promote_waiting --------------------------------------------------------------------------


def _waiting(i: int, credits: int, requested_by: Any = "u1") -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID(int=i), kind="web_refresh", credits_estimated=credits, requested_by=requested_by
    )


def _promote(waiting: list[Any], world: Any, reserved: int = 0) -> tuple[list[UUID], _Session]:
    held = {"reserved": reserved}

    def on_entry(params: dict[str, Any] | None) -> list[Any]:
        assert params is not None and params["k"] == "reserve"
        held["reserved"] += params["c"]
        return []

    session = _Session(
        {
            Q.LOCK: [],
            Q.WAITING: waiting,
            Q.POOL: lambda p: _pool_row(reserved=held["reserved"]),
            Q.QUEUE: [],
            Q.ENTRY: on_entry,
        }
    )
    return _run(CR.promote_waiting(session, NOW)), session


def test_waiting_jobs_are_queued_oldest_first_while_the_pool_covers_them(world: Any) -> None:
    world.pool = 100
    queued, session = _promote([_waiting(1, 60), _waiting(2, 40), _waiting(3, 1)], world)
    assert queued == [UUID(int=1), UUID(int=2)]  # the third no longer fits: 100 - 60 - 40 = 0
    assert session.statements()[:2] == [Q.LOCK, Q.WAITING]
    assert session.params_of(Q.QUEUE) == [
        {"p": "2026-10", "j": UUID(int=1)},
        {"p": "2026-10", "j": UUID(int=2)},
    ]
    assert session.params_of(Q.ENTRY) == [
        {
            "p": "2026-10",
            "k": "reserve",
            "c": 60,
            "j": UUID(int=1),
            "pu": "web_refresh",
            "a": "u1",
            "now": NOW,
        },
        {
            "p": "2026-10",
            "k": "reserve",
            "c": 40,
            "j": UUID(int=2),
            "pu": "web_refresh",
            "a": "u1",
            "now": NOW,
        },
    ]
    assert world.settings_calls >= 1 and all(s is session for s in world.sessions)


def test_a_job_that_does_not_fit_stops_the_queue_and_one_that_just_fits_goes_in(world: Any) -> None:
    world.pool = 50
    queued, _ = _promote([_waiting(1, 51), _waiting(2, 10)], world)
    assert queued == []  # the oldest does not fit, the later one waits behind it
    queued, _ = _promote([_waiting(1, 50)], world)
    assert queued == [UUID(int=1)]


def test_no_waiting_job_is_nothing_to_do(world: Any) -> None:
    queued, session = _promote([], world)
    assert queued == [] and session.statements() == [Q.LOCK, Q.WAITING]


def test_the_requester_of_a_promoted_job_is_recorded_as_text(world: Any) -> None:
    _, session = _promote([_waiting(1, 5, requested_by=UUID(int=9))], world)
    assert session.params_of(Q.ENTRY)[0]["a"] == str(UUID(int=9))  # type: ignore[index]


# ---- claim and the run link -------------------------------------------------------------------


def test_claim_moves_a_queued_job_to_running() -> None:
    row = SimpleNamespace(id=JOB)
    session = _Session({Q.CLAIM: [row]})
    assert _run(CR.claim(session, JOB, NOW)) is row
    assert session.calls == [(Q.CLAIM, {"j": JOB, "now": NOW})]
    assert _run(CR.claim(_Session({Q.CLAIM: []}), JOB, NOW)) is None


def test_unstarted_jobs_are_put_back_with_their_ids() -> None:
    session = _Session(
        {Q.REQUEUE: [SimpleNamespace(id=str(JOB)), SimpleNamespace(id=str(OTHER_JOB))]}
    )
    assert _run(CR.requeue_unstarted(session, NOW, 7)) == [JOB, OTHER_JOB]
    assert session.calls == [(Q.REQUEUE, {"b": NOW, "n": 7})]


def test_a_job_is_linked_to_its_run() -> None:
    session = _Session({Q.LINK_RUN: []})
    _run(CR.link_run(session, JOB, RUN))
    assert session.calls == [(Q.LINK_RUN, {"r": RUN, "j": JOB})]


def test_starting_with_a_run_needs_a_job_that_is_waiting_to_run() -> None:
    session = _Session({Q.START_WITH_RUN: JOB})
    _run(CR.start_with_run(session, JOB, RUN, NOW))
    assert session.calls == [(Q.START_WITH_RUN, {"r": RUN, "j": JOB, "now": NOW})]
    with pytest.raises(CR.JobOpen) as caught:
        _run(CR.start_with_run(_Session({Q.START_WITH_RUN: None}), JOB, RUN, NOW))
    assert str(caught.value) == "the job is not waiting to run"


# ---- settle_run -------------------------------------------------------------------------------


def _run_job(status: str, reserved: int = 20, used: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        id=str(JOB),
        kind="web_refresh",
        account_id=str(ACCOUNT),
        requested_by="u1",
        status=status,
        credits_estimated=reserved,
        credits_used=used,
    )


def _settle_run(
    job: Any, run_status: str, spent: str = "3", fault: bool = False
) -> tuple[Any, _Session]:
    session = _Session(
        {
            Q.JOB_OF_RUN: [job] if job is not None else [],
            Q.SELECT_JOB: [_job("running", credits=20)],
            Q.ENTRY: [],
            Q.UPDATE_JOB: [],
        }
    )
    out = _run(
        CR.settle_run(
            session,
            RUN,
            run_status=run_status,
            spent_usd=Decimal(spent),
            platform_fault=fault,
            now=NOW,
        )
    )
    return out, session


def test_a_run_without_a_job_settles_nothing() -> None:
    out, session = _settle_run(None, "COMPLETED")
    assert out is None and session.calls == [(Q.JOB_OF_RUN, {"r": RUN})]


def test_a_run_already_settled_reports_what_the_job_holds_as_a_replay() -> None:
    out, session = _settle_run(_run_job("done", reserved=20, used=6), "COMPLETED")
    assert out == CR.RunSettlement(JOB, "web_refresh", ACCOUNT, "u1", "done", 20, 6, True)
    assert out.released == 14
    assert session.statements() == [Q.JOB_OF_RUN]
    for ended in ("failed", "canceled"):
        replay, again = _settle_run(_run_job(ended, reserved=20, used=0), "COMPLETED")
        assert replay is not None and replay.replayed and replay.status == ended
        assert again.statements() == [Q.JOB_OF_RUN]


@pytest.mark.parametrize(
    ("run_status", "job_status", "reason", "stop"),
    [
        ("COMPLETED", "done", None, None),
        ("PARTIAL", "done", None, None),
        ("BUDGET_EXCEEDED", "done", None, "credit_cap_reached"),
        ("FAILED", "failed", "The research did not finish.", None),
        ("CANCELED", "canceled", "The research did not finish.", None),
    ],
)
def test_a_run_ends_its_job_with_the_matching_status_reason_and_stop(
    run_status: str, job_status: str, reason: str | None, stop: str | None
) -> None:
    out, session = _settle_run(_run_job("running"), run_status)
    assert out == CR.RunSettlement(JOB, "web_refresh", ACCOUNT, "u1", job_status, 20, 6, False)
    assert out.released == 14
    assert session.params_of(Q.SELECT_JOB) == [{"j": JOB}]
    assert session.params_of(Q.UPDATE_JOB) == [
        {
            "st": job_status,
            "u": 6,
            "now": NOW,
            "r": reason,
            "sr": stop,
            "j": JOB,
        }
    ]


def test_a_platform_fault_costs_the_client_nothing() -> None:
    out, session = _settle_run(_run_job("running"), "FAILED", spent="40", fault=True)
    assert out is not None and out.used == 0 and out.status == "failed"
    assert [p["k"] for p in session.params_of(Q.ENTRY)] == ["release"]  # type: ignore[index]


def test_a_library_add_is_a_finished_job_that_used_nothing() -> None:
    session = _Session({Q.LIBRARY_ADD: JOB})
    assert _run(CR.record_library_add(session, ACCOUNT, "u1", NOW)) == JOB
    assert session.calls == [
        (Q.LIBRARY_ADD, {"a": ACCOUNT, "p": "2026-10", "by": "u1", "now": NOW})
    ]


# ---- ledger_problems --------------------------------------------------------------------------


def _ledger_row(
    kind: str = "full",
    status: str = "done",
    est: int = 10,
    used: int = 6,
    reserved: int = 10,
    released: int = 10,
    settled: int = 6,
    i: int = 1,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=UUID(int=i),
        kind=kind,
        status=status,
        credits_estimated=est,
        credits_used=used,
        reserved=reserved,
        released=released,
        settled=settled,
    )


def _problems(rows: list[Any], periods: list[Any] | None = None) -> list[str]:
    session = _Session({Q.LEDGER: rows, Q.HELD: periods or []})
    out = _run(CR.ledger_problems(session))
    assert session.statements() == [Q.LEDGER, Q.HELD]
    return out


def _id(i: int = 1) -> str:
    return str(UUID(int=i))


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        (_ledger_row(), []),  # a settled job: reserved, released in full, settled equals used
        (
            _ledger_row(
                kind="library_add",
                status="done",
                est=0,
                used=0,
                reserved=-1,
                released=-1,
                settled=0,
            ),
            [],
        ),
        (
            _ledger_row(kind="library_add", est=0, used=0, reserved=0, released=-1, settled=0),
            ["library_add_moved_credits"],
        ),
        (
            _ledger_row(kind="library_add", est=0, used=0, reserved=-1, released=0, settled=0),
            ["library_add_moved_credits"],
        ),
        (
            _ledger_row(kind="library_add", est=0, used=0, reserved=-1, released=-1, settled=1),
            ["library_add_moved_credits"],
        ),
        (
            _ledger_row(kind="library_add", est=0, used=1, reserved=-1, released=-1, settled=0),
            ["library_add_moved_credits"],
        ),
        (
            _ledger_row(
                status="waiting_credit_limit", est=10, used=0, reserved=-1, released=-1, settled=0
            ),
            [],
        ),
        (
            _ledger_row(status="waiting_credit_limit", used=0, reserved=0, released=-1, settled=0),
            ["waiting_job_holds_credits"],
        ),
        (
            _ledger_row(status="waiting_credit_limit", used=0, reserved=-1, released=0, settled=0),
            ["waiting_job_holds_credits"],
        ),
        (
            _ledger_row(status="waiting_credit_limit", used=0, reserved=-1, released=-1, settled=1),
            ["waiting_job_holds_credits"],
        ),
        (_ledger_row(status="queued", used=0, reserved=10, released=-1, settled=0), []),
        (_ledger_row(status="running", used=0, reserved=10, released=-1, settled=0), []),
        (
            _ledger_row(status="queued", used=0, reserved=9, released=-1, settled=0),
            ["open_job_reserve_wrong"],
        ),
        (
            _ledger_row(status="running", used=0, reserved=10, released=0, settled=0),
            ["open_job_settled"],
        ),
        (
            _ledger_row(status="running", used=0, reserved=10, released=-1, settled=1),
            ["open_job_settled"],
        ),
        (_ledger_row(reserved=10, released=9), ["release_differs_from_reserve"]),
        (_ledger_row(reserved=-1, released=0, settled=0, used=0), ["settled_without_reserve"]),
        (
            _ledger_row(reserved=-1, released=-1, settled=3, used=3),
            ["settled_without_reserve", "settled_more_than_reserved"],
        ),
        (
            _ledger_row(reserved=-1, released=-1, settled=1, used=1),
            ["settled_without_reserve", "settled_more_than_reserved"],
        ),
        (
            _ledger_row(reserved=10, released=10, settled=11, used=11),
            ["settled_more_than_reserved"],
        ),
        (_ledger_row(reserved=10, released=10, settled=6, used=7), ["used_differs_from_settled"]),
        (
            _ledger_row(reserved=-1, released=-1, settled=0, used=0),
            [],
        ),  # an ended job that never held
    ],
)
def test_each_broken_ledger_rule_is_named_with_the_job_id(
    row: SimpleNamespace, expected: list[str]
) -> None:
    assert _problems([row]) == [f"{name}:{_id()}" for name in expected]


def test_a_library_add_does_not_end_the_check_of_the_jobs_after_it() -> None:
    library = _ledger_row(
        i=1, kind="library_add", est=0, used=0, reserved=-1, released=-1, settled=0
    )
    broken = _ledger_row(i=2, reserved=10, released=9)
    assert _problems([library, broken]) == [f"release_differs_from_reserve:{_id(2)}"]


def test_the_problems_of_several_jobs_come_in_the_order_of_the_rows() -> None:
    rows = [
        _ledger_row(i=2, status="queued", used=0, reserved=9, released=-1, settled=0),
        _ledger_row(i=1, reserved=10, released=9),
    ]
    assert _problems(rows) == [
        f"open_job_reserve_wrong:{_id(2)}",
        f"release_differs_from_reserve:{_id(1)}",
    ]


def test_a_period_that_holds_a_negative_amount_is_named() -> None:
    periods = [
        SimpleNamespace(period="2026-09", held=-1),
        SimpleNamespace(period="2026-10", held=0),
        SimpleNamespace(period="2026-08", held=5),
    ]
    assert _problems([], periods) == ["negative_hold:2026-09"]
    assert _problems([_ledger_row(reserved=10, released=9)], periods) == [
        f"release_differs_from_reserve:{_id()}",
        "negative_hold:2026-09",
    ]


# ---- the account's labels ---------------------------------------------------------------------


def test_the_account_label_and_the_last_finish_are_read_by_account() -> None:
    row = SimpleNamespace(domain="acme.example", name="Acme")
    session = _Session({Q.ACCOUNT_LABEL: [row], Q.LAST_FINISHED: NOW})
    assert _run(CR.account_label(session, ACCOUNT)) is row
    assert _run(CR.last_finished(session, ACCOUNT)) == NOW
    assert session.calls == [(Q.ACCOUNT_LABEL, {"a": ACCOUNT}), (Q.LAST_FINISHED, {"a": ACCOUNT})]


def test_the_credits_short_error_says_so_and_carries_the_numbers() -> None:
    error = CR.CreditsShort(7, 3)
    assert str(error) == "credits short" and (error.needed, error.left) == (7, 3)


def test_the_models_json_and_job_end_table_are_these() -> None:
    assert CR.JOB_END == {
        "COMPLETED": "done",
        "PARTIAL": "done",
        "BUDGET_EXCEEDED": "done",
        "FAILED": "failed",
        "CANCELED": "canceled",
    }
    assert CR.SCOPES == ("jobs", "people", "full", "topic")
    assert set(CR.PURPOSE) == {
        "web_refresh",
        "scheduled_refresh",
        "new_research",
        "file_analysis",
        "library_add",
    }
    assert json.dumps({"a": 1}) == '{"a": 1}'
