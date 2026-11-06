"""The schedule tick's start budget (fix prompt A, task 4): a fair round-robin order across
tenants and a ceiling on the starts of one tick, overall and per tenant. Pure; the PostgreSQL test
with 25 due accounts across 4 tenants is tests/replica/test_schedule_tick_bounds.py."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from account_intelligence.replica.schedules import StartBudget, fair_order


def _due(counts: dict[str, int]) -> tuple[list[tuple[UUID, UUID]], dict[str, UUID]]:
    tenants = {name: uuid4() for name in counts}
    rows = [(tenants[name], uuid4()) for name, n in counts.items() for _ in range(n)]
    return rows, tenants


def test_fair_order_takes_one_account_per_tenant_per_round_keeping_each_tenants_order() -> None:
    rows, t = _due({"a": 3, "b": 1, "c": 2})
    ordered = fair_order(rows)
    assert sorted(ordered) == sorted(rows)
    assert [tenant for tenant, _ in ordered] == [t["a"], t["b"], t["c"], t["a"], t["c"], t["a"]]
    # within a tenant the lookup's own order (oldest due first) is kept
    for tenant in t.values():
        assert [a for x, a in ordered if x == tenant] == [a for x, a in rows if x == tenant]


def test_fair_order_of_nothing_is_nothing() -> None:
    assert fair_order([]) == []


def test_budget_bounds_the_tick_overall_and_per_tenant() -> None:
    budget = StartBudget(max_starts=4, max_per_tenant=2)
    a, b, c = uuid4(), uuid4(), uuid4()
    assert budget.room(a) == 2
    budget.take(a)
    budget.take(a)
    assert budget.room(a) == 0 and budget.room(b) == 2 and not budget.exhausted
    budget.take(b)
    assert budget.room(c) == 1  # one start left overall
    budget.take(c)
    assert budget.exhausted and budget.room(b) == 0 and budget.started == 4
    with pytest.raises(RuntimeError):
        budget.take(b)


@pytest.mark.parametrize(("starts", "per_tenant"), [(0, 3), (10, 0), (-1, 3)])
def test_budget_refuses_a_non_positive_ceiling(starts: int, per_tenant: int) -> None:
    with pytest.raises(ValueError):
        StartBudget(max_starts=starts, max_per_tenant=per_tenant)
