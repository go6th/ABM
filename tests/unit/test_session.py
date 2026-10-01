"""Offline unit tests for db/session.py using scripted fake sessions.

These verify the transaction-local tenant-context contract (I1) and the fail-closed
role-safety checks (I3) without a live database; the live behaviour is asserted again
by tests/isolation/test_rls.py.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from abm_generator.db import session as session_mod

TID = uuid.uuid4()


class FakeResult:
    """Mimics SQLAlchemy Result for .scalar_one() / .first() / .scalars().all()."""

    def __init__(self, value: Any = None, rows: list[Any] | None = None) -> None:
        self._value = value
        self._rows = rows if rows is not None else ([value] if value is not None else [])

    def scalar_one(self) -> Any:
        return self._value

    def first(self) -> Any:
        return self._rows[0] if self._rows else None

    def scalars(self) -> Any:
        return SimpleNamespace(all=lambda: list(self._rows))


class FakeSession:
    def __init__(self, results: list[Any] | None = None) -> None:
        self.executed: list[tuple[Any, dict[str, Any] | None]] = []
        self._results = list(results or [])
        self.committed = False
        self.rolled_back = False

    def _next(self) -> FakeResult:
        if self._results:
            item = self._results.pop(0)
            return item if isinstance(item, FakeResult) else FakeResult(item)
        return FakeResult(None)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.executed.append((stmt, params))
        return self._next()

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def begin(self) -> Any:
        return self


def _patch_maker(monkeypatch: pytest.MonkeyPatch, session: FakeSession) -> list[FakeSession]:
    created: list[FakeSession] = []

    def fake_maker() -> FakeSession:
        s = FakeSession(session._results)
        created.append(s)
        return s

    monkeypatch.setattr(session_mod, "get_sessionmaker", lambda: fake_maker)
    return created


async def test_set_tenant_stmt_binds_setting_and_uuid() -> None:
    stmt = session_mod.set_tenant_stmt(TID)
    assert "set_config" in str(stmt)
    params = stmt.compile().params
    assert params == {"setting": "app.tenant_id", "tid": str(TID)}


async def test_tenant_txn_sets_context_then_yields_session(monkeypatch: pytest.MonkeyPatch) -> None:
    holder = FakeSession()
    created = _patch_maker(monkeypatch, holder)

    async with session_mod.tenant_txn(TID) as s:
        assert s is not None

    assert len(created) == 1
    stmts = [str(stmt) for stmt, _ in created[0].executed]
    assert any("set_config" in q for q in stmts), "tenant context must be set in-txn (I1)"
    params = created[0].executed[0][1]
    assert params == {"setting": "app.tenant_id", "tid": str(TID)}


async def test_run_in_tenant_returns_fn_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_maker(monkeypatch, FakeSession())

    async def fn(session: Any) -> str:
        return f"ok-{type(session).__name__}"

    assert await session_mod.run_in_tenant(TID, fn) == "ok-FakeSession"


async def test_unset_txn_yields_session_without_context_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _patch_maker(monkeypatch, FakeSession())
    async with session_mod.unset_txn() as s:
        assert s is not None
    assert created[0].executed == [], "unset_txn must not set any tenant context"


async def test_current_tenant_setting_returns_none_for_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession([FakeResult("")])
    val = await session_mod.current_tenant_setting(session)  # type: ignore[arg-type]
    assert val is None


async def test_current_tenant_setting_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession([FakeResult(str(TID))])
    val = await session_mod.current_tenant_setting(session)  # type: ignore[arg-type]
    assert val == str(TID)


async def test_role_safety_report_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession(
        [
            FakeResult("app_rw"),  # current_user
            FakeResult(False),  # rolbypassrls
            FakeResult(False),  # rolsuper
            FakeResult(rows=[]),  # owned tables
        ]
    )
    report = await session_mod.check_app_role_is_safe(session)  # type: ignore[arg-type]
    assert report["current_user"] == "app_rw"
    assert report["tables_owned"] == []


async def test_role_safety_raises_on_bypassrls() -> None:
    session = FakeSession([FakeResult("app_rw"), FakeResult(True)])
    with pytest.raises(RuntimeError, match="BYPASSRLS"):
        await session_mod.check_app_role_is_safe(session)  # type: ignore[arg-type]


async def test_role_safety_raises_on_superuser() -> None:
    session = FakeSession([FakeResult("postgres"), FakeResult(False), FakeResult(True)])
    with pytest.raises(RuntimeError, match="superuser"):
        await session_mod.check_app_role_is_safe(session)  # type: ignore[arg-type]


async def test_role_safety_raises_on_owned_tables() -> None:
    session = FakeSession(
        [FakeResult("app_rw"), FakeResult(False), FakeResult(False), FakeResult(rows=["tenants"])]
    )
    with pytest.raises(RuntimeError, match="owns tables"):
        await session_mod.check_app_role_is_safe(session)  # type: ignore[arg-type]


async def test_engine_singletons_and_dispose(monkeypatch: pytest.MonkeyPatch) -> None:
    created_urls: list[str] = []

    class FakeEngine:
        def __init__(self, url: str) -> None:
            self.url = url
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    def fake_create(url: str, **kwargs: Any) -> FakeEngine:
        created_urls.append(url)
        return FakeEngine(url)

    monkeypatch.setattr(session_mod, "create_async_engine", fake_create)
    monkeypatch.setattr(session_mod, "_ENGINE", None)
    monkeypatch.setattr(session_mod, "_SESSIONMAKER", None)

    engine = session_mod.get_engine()
    assert engine is session_mod.get_engine(), "engine must be a singleton"
    assert created_urls == [engine.url]

    maker = session_mod.get_sessionmaker()
    assert maker is session_mod.get_sessionmaker(), "sessionmaker must be a singleton"

    await session_mod.dispose_engine()
    assert engine.disposed is True
    monkeypatch.setattr(session_mod, "_ENGINE", None)
    monkeypatch.setattr(session_mod, "_SESSIONMAKER", None)
