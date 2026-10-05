"""Kill a worker subprocess inside the billing window, resume in another worker.

The child is terminated with ``os._exit(137)`` right after the provider responded and
before the ledger was finalised (the window of known defect 3), or after the second
page of a paged feed. A second worker (different owner, clock past the lease expiry)
resumes. Asserted: at most one repeated provider call, ``possible_duplicate`` set on
the repeat, page continuity, and ``spent_usd`` equal to the ledger sum.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.factory import build_runtime
from account_intelligence.connectors.models import FeedArgs, WebSearchArgs
from account_intelligence.connectors.payloads import LocalPayloadStore
from account_intelligence.connectors.providers.p_signals import SignalsConnector
from account_intelligence.connectors.security import UrlPolicy
from account_intelligence.connectors.service import call_operation
from account_intelligence.connectors.settings import load_providers_config
from account_intelligence.db.session import tenant_txn
from tests.connectors import kill_worker
from tests.connectors.conftest import (
    FakeClock,
    FakeSleep,
    context,
    event_rows,
    ledger_rows,
    make_run,
    run_row,
)

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]
REPO = Path(__file__).resolve().parents[2]


def _run_child(
    mode: str, tenant: UUID, account: UUID, run: UUID, domain: str, calls: Path, store: Path
) -> int:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.connectors.kill_worker",
            mode,
            str(tenant),
            str(account),
            str(run),
            domain,
            str(calls),
            str(store),
        ],
        cwd=REPO,
        env=os.environ.copy(),
        capture_output=True,
        timeout=120,
    )
    return proc.returncode


def _resumer(calls: Path, store: Path):  # type: ignore[no-untyped-def]
    cfg = load_providers_config()
    # Another worker whose clock is past the dead worker's lease expiry.
    clock = FakeClock(datetime.now(UTC) + timedelta(seconds=cfg.defaults.ledger.lease_ttl_s + 5))
    tr = kill_worker.transport(calls, total_pages=3)
    rt = build_runtime(
        cfg,
        txn=tenant_txn,
        payloads=LocalPayloadStore(store),
        transports={code: tr for code in ProviderCode},
        env={"P_SEARCH_API_KEY": "k", "P_SIGNALS_API_KEY": "k"},
        clock=clock,
        sleep=FakeSleep(clock),
        url_policy=UrlPolicy(resolver=kill_worker._public),
        worker_id="worker-resumer",
    )
    return replace(
        rt, connectors={**rt.connectors, ProviderCode.P_SIGNALS: SignalsConnector(page_limit=1)}
    )


async def test_kill_between_provider_response_and_ledger_write(
    tenant_a: UUID, tmp_path: Path
) -> None:
    account, run, domain = make_run(tenant_a)
    calls, store = tmp_path / "calls.log", tmp_path / "payloads"
    assert _run_child("after_response", tenant_a, account, run, domain, calls, store) == 137
    assert len(calls.read_text().splitlines()) == 1  # the provider answered (and may have billed)
    assert [e["event"] for e in event_rows(run)] == ["started"]  # the crash window, recorded
    assert ledger_rows(run) == []

    rt = _resumer(calls, store)
    ctx = context(tenant_a, account, run, domain)
    result = await call_operation(
        Operation.WEB_SEARCH, WebSearchArgs(queries=("acme funding",)), ctx, rt
    )
    assert result.status == "done" and result.possible_duplicate is True
    assert len(calls.read_text().splitlines()) == 2  # at most one repeat
    assert [(e["attempt"], e["event"], e["possible_duplicate"]) for e in event_rows(run)] == [
        (1, "started", False),
        (1, "abandoned", True),
        (2, "started", True),
        (2, "done", True),
    ]
    assert ledger_rows(run)[0]["possible_duplicate"] is True
    assert (
        Decimal(run_row(run)["spent_usd"]) == await rt.ledger.ledger_sum(ctx) == Decimal("0.010000")
    )
    # a further identical call is a pure replay
    await call_operation(Operation.WEB_SEARCH, WebSearchArgs(queries=("acme funding",)), ctx, rt)
    assert len(calls.read_text().splitlines()) == 2


async def test_kill_mid_pagination_resumes_with_page_continuity(
    tenant_a: UUID, tmp_path: Path
) -> None:
    account, run, domain = make_run(tenant_a)
    calls, store = tmp_path / "calls.log", tmp_path / "payloads"
    assert _run_child("mid_pagination", tenant_a, account, run, domain, calls, store) == 137
    pages_before = [line.split("page=")[1].split("&")[0] for line in calls.read_text().splitlines()]
    assert pages_before == ["1", "2"]
    assert len(ledger_rows(run)) == 1  # page 1 finalised, page 2 in the crash window

    rt = _resumer(calls, store)
    ctx = context(tenant_a, account, run, domain)
    result = await call_operation(Operation.NEWS_EVENTS, FeedArgs(domain=domain), ctx, rt)
    pages_all = [line.split("page=")[1].split("&")[0] for line in calls.read_text().splitlines()]
    assert pages_all == ["1", "2", "2", "3"]  # page 1 replayed from storage, page 2 repeated once
    assert result.status == "done" and result.counts.pages == 3
    assert [r.dedupe_key() for r in result.records] == ["event-1", "event-2", "event-3"]
    assert result.possible_duplicate is True
    assert sum(1 for r in ledger_rows(run) if r["possible_duplicate"]) == 1
    assert Decimal(run_row(run)["spent_usd"]) == await rt.ledger.ledger_sum(ctx)
