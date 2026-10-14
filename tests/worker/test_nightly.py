"""The nightly ``spend-invariants`` command checks every tenant's ledgers and fails only on a
broken rule (SYNTHETIC tenants; the database tests plant one broken credit ledger).

GUARANTEE(test_the_nightly_check_flags_a_broken_credit_ledger)
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.worker import nightly
from account_intelligence.worker.__main__ import main


def _report(**kw: object) -> nightly.InvariantReport:
    report = nightly.InvariantReport()
    for k, v in kw.items():
        setattr(report, k, v)
    return report


def test_the_command_exits_zero_when_nothing_is_broken(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def clean(*_a: object, **_k: object) -> nightly.InvariantReport:
        return _report(runs_checked=3, tenants_checked=2)

    monkeypatch.setattr(nightly, "spend_invariants", clean)
    assert main(["spend-invariants"]) == 0
    assert "runs_checked=3" in capsys.readouterr().out


def test_the_command_exits_one_on_a_broken_rule(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def broken(*_a: object, **_k: object) -> nightly.InvariantReport:
        return _report(credit_problems=["t:release_differs_from_reserve:j"])

    monkeypatch.setattr(nightly, "spend_invariants", broken)
    assert main(["spend-invariants"]) == 1
    assert "broken t:release_differs_from_reserve:j" in capsys.readouterr().out


def test_the_command_says_when_it_could_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(*_a: object, **_k: object) -> nightly.InvariantReport:
        raise OSError("no database")

    monkeypatch.setattr(nightly, "spend_invariants", down)
    assert main(["spend-invariants"]) == 3


def test_the_command_takes_no_arguments() -> None:
    assert main(["spend-invariants", "--tenant-id", str(uuid4())]) == 2


def _tenant_with_job(admin: str, *, reserve: int, release: int | None, settle: int) -> UUID:
    with psycopg.connect(admin, autocommit=True) as c:
        t = c.execute("INSERT INTO tenants (name) VALUES ('synthetic') RETURNING id").fetchone()[0]
        a = c.execute(
            "INSERT INTO accounts (tenant_id, owner_user_id, domain)"
            " VALUES (%s, 'test-owner', 'acme.example') RETURNING id",
            (t,),
        ).fetchone()[0]
        j = c.execute(
            "INSERT INTO research_jobs (tenant_id, account_id, kind, scope, status, period,"
            " credits_estimated, credits_used, requested_by, finished_at) VALUES (%s, %s,"
            " 'web_refresh', 'jobs', 'done', to_char(now(), 'YYYY-MM'), %s, %s, 'u', now())"
            " RETURNING id",
            (t, a, reserve, settle),
        ).fetchone()[0]
        moves = [("reserve", reserve), ("settle", settle)]
        if release is not None:
            moves.append(("release", release))
        for kind, credits in moves:
            if credits:
                c.execute(
                    "INSERT INTO credit_entries (tenant_id, period, kind, credits, job_id, purpose,"
                    " actor) VALUES (%s, to_char(now(), 'YYYY-MM'), %s, %s, %s, 'web_refresh',"
                    " 'system')",
                    (t, kind, credits, j),
                )
    return UUID(str(t))


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_the_nightly_check_flags_a_broken_credit_ledger() -> None:
    from account_intelligence.db.session import SessionLocal, tenant_txn
    from tests.conftest import ADMIN_DSN

    good = _tenant_with_job(ADMIN_DSN, reserve=20, release=20, settle=12)
    bad = _tenant_with_job(ADMIN_DSN, reserve=20, release=None, settle=25)
    report = await nightly.spend_invariants(SessionLocal, tenant_txn, datetime.now(UTC))
    mine = [p for p in report.credit_problems if p.startswith(str(bad))]
    assert {p.split(":")[1] for p in mine} == {
        "release_differs_from_reserve",
        "settled_more_than_reserved",
    }
    assert not [p for p in report.credit_problems if p.startswith(str(good))]
    assert report.tenants_checked >= 2 and not report.ok
