"""Less-travelled production adapter paths: builder, gate, wallet, approvals without a cap, the
tracker's partial downgrade and reason mapping (SYNTHETIC only)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from account_intelligence.platform.ports import NotConfigured
from agents.config import load_skill_config
from agents.ports import RunContext
from agents.production.credit import CreditAdapter
from agents.production.delivery import ExportBuilderPort, GateAdapter, RunnerSettles
from spend import pause

pytestmark = pytest.mark.asyncio


def ctx() -> RunContext:
    return RunContext(uuid4(), uuid4(), "acme.example", "Acme", "acme.example", False, "sk-1")


@dataclass(frozen=True)
class _Clock:
    at: datetime

    def now(self) -> datetime:
        return self.at


@dataclass
class _Builder:
    calls: int = 0

    async def build(self, tenant: str, run: str) -> list[str]:
        self.calls += 1
        return [str(uuid4()), str(uuid4())]


@dataclass
class _Gate:
    failing: dict[str, list[str]] = field(default_factory=dict)

    async def check(self, tenant: str, run: str) -> dict[str, list[str]]:
        return self.failing


@dataclass
class _Deps:
    builder: Any = None
    gate: Any = None
    payloads: Any = None
    rows: dict[str, Any] = field(default_factory=dict)

    def txn(self, tenant: UUID) -> Any:
        rows = self.rows

        @asynccontextmanager
        async def session() -> Any:
            class _Result:
                def __init__(self, value: Any) -> None:
                    self.value = value

                def scalar_one_or_none(self) -> Any:
                    return self.value

                def first(self) -> Any:
                    return self.value

            class _Session:
                async def execute(self, statement: Any, params: Any = None) -> Any:
                    sql = str(statement)
                    for key, value in rows.items():
                        if key in sql:
                            return _Result(value)
                    return _Result(None)

            yield _Session()

        return session()


async def test_builder_needs_a_configured_builder_and_builds_once() -> None:
    with pytest.raises(NotConfigured):
        await ExportBuilderPort(_Deps()).build(ctx(), "client")  # type: ignore[arg-type]
    builder = _Builder()
    port = ExportBuilderPort(_Deps(builder=builder))  # type: ignore[arg-type]
    c = ctx()
    first = await port.build(c, "client")
    assert await port.build(c, "client") == first and builder.calls == 1
    assert await port.build(c, "internal") == ()  # no Internal Only file builder yet


async def test_builder_reads_stored_member_texts_and_refuses_unknown_files() -> None:
    import io
    import zipfile

    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as z:
        z.writestr("word/document.xml", "<w:t>hello</w:t>")

    class _Store:
        async def get(self, key: str) -> bytes:
            return blob.getvalue() if key == "k1" else b"plain text body"

    port = ExportBuilderPort(_Deps(payloads=_Store(), rows={"deliverables": "k1"}))  # type: ignore[arg-type]
    assert "<w:t>hello</w:t>" in await port.texts(ctx(), str(uuid4()))
    plain = ExportBuilderPort(_Deps(payloads=_Store(), rows={"deliverables": "k2"}))  # type: ignore[arg-type]
    assert await plain.texts(ctx(), str(uuid4())) == ("plain text body",)
    with pytest.raises(KeyError):
        await ExportBuilderPort(_Deps(payloads=_Store())).texts(ctx(), str(uuid4()))  # type: ignore[arg-type]


async def test_gate_fails_closed_without_a_gate_and_keeps_only_failing_checks() -> None:
    assert await GateAdapter(_Deps()).check(ctx()) == {"gate_not_configured": ["quality gate"]}  # type: ignore[arg-type]
    gate = _Gate({"links": ["row 3"], "dashes": []})
    assert await GateAdapter(_Deps(gate=gate)).check(ctx()) == {"links": ["row 3"]}  # type: ignore[arg-type]


async def test_settlement_is_left_to_the_runner() -> None:
    assert await RunnerSettles().settle_once(ctx(), False) is False


async def test_no_cap_means_no_approval_request_and_no_proposal_without_deps() -> None:
    adapter = CreditAdapter(_Deps().txn, None, load_skill_config())  # type: ignore[arg-type]
    assert (
        await adapter.request_approval(ctx(), [{"row_key": "podcasts", "need": "web_search"}], 0)
        == "no_cap"
    )
    assert await adapter.propose_cap(ctx()) is False
    state = await adapter.cap_state(ctx())
    assert (state.cap_required, state.cap_version) == (True, None)  # unknown run: refused


async def test_credit_adapter_reads_the_cap_and_opens_a_pause_request(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from agents.production import credit as credit_module

    when = datetime(2026, 10, 8, 9, tzinfo=UTC)
    run_row = SimpleNamespace(
        spent_usd=Decimal("4.90"),
        held_usd=Decimal("0"),
        budget_usd=Decimal("5.00"),
        cap_version=3,
        pause_open=False,
        stopped=False,
    )
    deps = _Deps(rows={"spent_usd": run_row})
    opened: list[tuple[Any, Any, Any, Any]] = []
    request_id = uuid4()

    async def price(self: CreditAdapter, need: str) -> tuple[int, bool]:
        assert need == "web_search"
        return 250_000, True

    async def open_pause(
        session: Any, run_id: UUID, request: pause.PauseRequest, now: datetime, cfg: Any
    ) -> tuple[UUID, bool]:
        opened.append((session, run_id, request, now))
        return request_id, True

    monkeypatch.setattr(CreditAdapter, "price", price)
    monkeypatch.setattr(credit_module.pause, "open_pause", open_pause)
    adapter = CreditAdapter(deps.txn, _Clock(when), load_skill_config())  # type: ignore[arg-type]
    context = ctx()

    result = await adapter.request_approval(
        context, [{"row_key": "leadership_changes", "need": "web_search"}], 250_000
    )

    assert result == str(request_id)
    assert len(opened) == 1
    _, run_id, request, opened_at = opened[0]
    assert run_id == context.run_id
    assert request.cap_version == 3
    assert request.rows_at_risk == ("leadership_changes",)
    assert opened_at == when


async def test_tracker_partial_downgrade_keeps_blocked_and_unset_rows() -> None:
    from agents.production import tracker as tracker_module
    from agents.production.tracker import TrackerAdapter

    @dataclass
    class _Row:
        key: str
        status: Any
        reason_code: Any
        proof_calls: int = 0

    class _Repo:
        def __init__(self, session: Any) -> None:
            pass

        async def get_all_rows(self, run_id: UUID) -> list[_Row]:
            from account_intelligence.tracker.model import ReasonCode, Status

            return [
                _Row("a", Status.BLOCKED, ReasonCode.SOURCE_FAILED, 1),
                _Row("b", None, None),
                _Row("c", Status.PARTIAL, ReasonCode.NO_CAP, 2),
            ]

    original = tracker_module.CoverageRepo
    tracker_module.CoverageRepo = _Repo  # type: ignore[misc,assignment]
    try:
        adapter = TrackerAdapter(_Deps().txn, {})  # type: ignore[arg-type]
        c = ctx()
        assert await adapter.mark_partial(c, "a", "pull_not_approved") == (
            tracker_module.RowOutcome("blocked", "source_failed", 1)
        )
        assert (await adapter.mark_partial(c, "b", "pull_not_approved")).status is None
        assert (await adapter.mark_partial(c, "c", "pull_not_approved")).status == "partial"
        assert (await adapter.mark_partial(c, "zz", "pull_not_approved")).status is None
        rows = await adapter.rows(c)
        assert rows["a"].status == "blocked" and rows["b"].status is None
    finally:
        tracker_module.CoverageRepo = original  # type: ignore[misc]
