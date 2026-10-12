"""The grading adapters over a real ledger: what a source may and may not become.

A call that failed and was then retried successfully counts as obtained (its later success
stands); a ledger row that cannot be re-parsed yields no source (claims citing it are refused);
a provider record without a URL, or with a URL that does not normalise, is never a source.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import psycopg
import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.errors import RetryableProviderError
from account_intelligence.connectors.factory import build_connectors
from account_intelligence.connectors.models import WebSearchArgs
from account_intelligence.connectors.service import call_operation
from account_intelligence.connectors.settings import load_providers_config
from account_intelligence.db.session import tenant_txn
from account_intelligence.grading.adapters import LedgerAdapter
from account_intelligence.grading.config import load_grading_config
from account_intelligence.grading.ports import RunInfo
from tests.conftest import ADMIN_DSN
from tests.connectors.conftest import T0, Harness, Reply, context, make_run
from tests.connectors.conftest import harness as harness  # pytest fixture re-export

pytestmark = [pytest.mark.requires_postgres, pytest.mark.asyncio]

GOOD = "https://blog.example.net/acme-crm"


def _search(*urls: str) -> dict[str, object]:
    return {
        "search_id": "s1",
        "session_id": "x",
        "results": [
            {"url": u, "title": "Acme", "publish_date": None, "excerpts": ["Acme runs a CRM."]}
            for u in urls
        ],
    }


async def test_retried_calls_count_once_and_unusable_records_never_become_sources(
    harness: Harness, tenant_a: UUID
) -> None:
    account, run, domain = make_run(tenant_a, domain="acme.example")
    ctx = context(tenant_a, account, run, domain)
    harness.providers[ProviderCode.P_SEARCH].script(
        Reply(status=500, body={}),  # the first attempt fails ...
        Reply(body=_search(GOOD, "", "http://")),  # ... the retry succeeds
    )
    rt = harness.runtime()
    args = WebSearchArgs(queries=("acme crm vendor",))
    with pytest.raises(RetryableProviderError):
        await call_operation(Operation.WEB_SEARCH, args, ctx, rt)
    await call_operation(Operation.WEB_SEARCH, args, ctx, rt)
    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        # a ledger row whose stored response cannot be re-parsed (no payload, no arguments)
        c.execute(
            "INSERT INTO tool_calls (tenant_id, run_id, tool, args_hash, provider_code,"
            " operation, response) VALUES (%s, %s, 'web_search', %s, 'p_search', 'web_search',"
            " '{}')",
            (tenant_a, run, "f" * 64),
        )
    adapter = LedgerAdapter(
        tenant_txn,
        harness.payloads,
        build_connectors(load_providers_config()),
        load_grading_config(),
    )
    info = RunInfo(tenant_a, account, run, T0 - timedelta(hours=1), domain, "Acme")
    fetches = await adapter.fetches(info)
    retried = [f for f in fetches.values() if f.queries == ("acme crm vendor",)]
    assert len(retried) == 1 and retried[0].success  # the failure is superseded by the retry
    sources = await adapter.sources(info)
    assert [s.url for s in sources] == [GOOD]  # empty and malformed URLs are dropped
    assert all(s.call_ref != "f" * 64 for s in sources)  # the unparseable row yields nothing
