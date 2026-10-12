"""Production adapters, one by one (SYNTHETIC only): error mapping, items, payload isolation,
pricing, member texts, tracker reasons and inheritance, Conflicting supersession on Postgres."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest

from account_intelligence.connectors.base import (
    ConnectorResult,
    Operation,
    ProviderCode,
)
from account_intelligence.connectors.errors import (
    BudgetExceeded,
    CapRefused,
    CreditsExhausted,
    NotSupportedYet,
    RetryableProviderError,
    SchemaDriftError,
)
from account_intelligence.connectors.models import Provenance, TechDetectionRecord, WebPageRecord
from account_intelligence.platform.hermetic import MemoryPayloadStore
from account_intelligence.tracker.model import CallRecord, ReasonCode
from agents.ports import CapPause, ProviderFailed, RunContext, SourceItem
from agents.production import provider as provider_module
from agents.production.credit import CreditAdapter, pull_plan_items
from agents.production.delivery import member_texts
from agents.production.payloads import StorePayloads
from agents.production.provider import ConnectorProvider, items_of, reason_code
from agents.production.tracker import InheritingCallLedger, reason_for

T0 = datetime(2026, 10, 8, 9, tzinfo=UTC)


def ctx(tenant: UUID | None = None) -> RunContext:
    return RunContext(
        tenant or uuid4(), uuid4(), "acme.example", "Acme", "acme.example", False, "sk-1"
    )


def prov(url: str | None, op: Operation = Operation.WEB_SEARCH) -> Provenance:
    return Provenance(
        provider_code=ProviderCode.P_SEARCH,
        operation=op,
        source_url=url,
        retrieved_at=T0,
        raw_ref="raw/1",
        page=1,
        index=0,
    )


def page(url: str | None, text: str = "Acme opened an office in Pune in 2026.") -> WebPageRecord:
    return WebPageRecord(
        kind="web_page",
        provenance=prov(url),
        lineage_hint=None,
        url=url or "",
        title_text=None,
        publish_date=None,
        excerpts_text=(text,),
        full_content_text=None,
    )


# provider ---------------------------------------------------------------------------------------


def test_items_keep_links_and_mark_date_ranges() -> None:
    base = {k: None for k in TechDetectionRecord.model_fields}
    base.update(
        kind="tech_detection",
        provenance=prov("https://acme.example/t"),
        lineage_hint=None,
        detection_id="d1",
        technology_name="MuleSoft",
        first_seen_at="2026-01-01",
        last_seen_at="2026-09-01",
        score=0.9,
        source_count=1,
        behind_firewall=False,
        evidence_urls=(),
    )
    tech = TechDetectionRecord(**base)
    items = items_of((page("https://acme.example/n"), page(None), tech))
    assert [i.url for i in items] == ["https://acme.example/n", "https://acme.example/t"]
    assert items[0].match_kind is None and items[1].match_kind == "date_range"
    assert "Pune" in items[0].text


class _Runtime:
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "kind", "code"),
    [
        (CapRefused("no_cap"), CapPause, "no_cap"),
        (BudgetExceeded(), CapPause, "cap_reached"),
        (NotSupportedYet(), ProviderFailed, "not_supported_yet"),
        (CreditsExhausted(), ProviderFailed, "credits_exhausted"),
        (SchemaDriftError(), ProviderFailed, "schema_drift_error"),
    ],
)
async def test_connector_provider_maps_refusals_and_failures(
    monkeypatch, error, kind, code
) -> None:  # type: ignore[no-untyped-def]
    async def boom(*args: Any, **kw: Any) -> Any:
        raise error

    monkeypatch.setattr(provider_module, "call_need", boom)
    adapter = ConnectorProvider(_Runtime(), uuid4())  # type: ignore[arg-type]
    with pytest.raises(kind) as raised:
        await adapter.call(ctx(), "web_search", "acme news", "company_news")
    assert getattr(raised.value, "reason", None) == code


@pytest.mark.asyncio
async def test_retryable_errors_are_left_to_the_node_retry_policy(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def flaky(*args: Any, **kw: Any) -> Any:
        raise RetryableProviderError("busy")

    monkeypatch.setattr(provider_module, "call_need", flaky)
    with pytest.raises(RetryableProviderError):
        await ConnectorProvider(_Runtime(), uuid4()).call(  # type: ignore[arg-type]
            ctx(), "web_search", "q", "company_news"
        )


@pytest.mark.asyncio
async def test_blocked_routes_and_unplanned_needs_fail_with_neutral_reasons(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    async def blocked(*args: Any, **kw: Any) -> ConnectorResult:
        return ConnectorResult(status="blocked", blocked_reason="p_search.web_search: down")

    monkeypatch.setattr(provider_module, "call_need", blocked)
    adapter = ConnectorProvider(_Runtime(), uuid4())  # type: ignore[arg-type]
    for need, row, code in (
        ("web_search", "company_news", "route_blocked"),
        ("teleport", "company_news", "need_not_routed"),
        ("profile_enrichment", "posts_priority_people", "needs_profile_urls"),
    ):
        with pytest.raises(ProviderFailed) as raised:
            await adapter.call(ctx(), need, "q", row)
        assert raised.value.reason == code and "p_" not in raised.value.reason


@pytest.mark.asyncio
async def test_page_reads_search_the_account_site_and_read_only_on_domain(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: list[tuple[str, Any, Any]] = []

    async def fake(route: str, args: Any, cc: Any, rt: Any, *, operation: Any = None) -> Any:
        seen.append((route, args, operation))
        if route == "open_web_search":
            return ConnectorResult(
                status="ok",
                records=(page("https://other.example/x"), page("https://jobs.acme.example/c")),
                call_refs=("s1",),
            )
        return ConnectorResult(
            status="ok",
            records=(page("https://jobs.acme.example/c"),),
            call_refs=("r1",),
            charged_usd=0.002,
        )

    monkeypatch.setattr(provider_module, "call_need", fake)
    result = await ConnectorProvider(_Runtime(), uuid4()).call(  # type: ignore[arg-type]
        ctx(), "scrape", "acme careers", "job_board_by_lane"
    )
    assert [s[0] for s in seen] == ["open_web_search", "job_boards_and_pages"]
    assert seen[0][1].include_domains == ("acme.example",)
    assert seen[1][1].url == "https://jobs.acme.example/c"  # never other.example
    assert result.call_refs == ("s1", "r1") and result.cost_micros == 2000


@pytest.mark.asyncio
async def test_feeds_use_the_row_operation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ops: list[Any] = []

    async def fake(route: str, args: Any, cc: Any, rt: Any, *, operation: Any = None) -> Any:
        ops.append((route, args.domain, operation))
        return ConnectorResult(status="ok")

    monkeypatch.setattr(provider_module, "call_need", fake)
    adapter = ConnectorProvider(_Runtime(), uuid4())  # type: ignore[arg-type]
    for row in ("company_news", "job_postings_12_months", "technology_detections"):
        result = await adapter.call(ctx(), "structured_signals", "q", row)
        assert result.items == () and result.result_count == 0
    assert [o[2] for o in ops] == [
        Operation.NEWS_EVENTS,
        Operation.JOB_OPENINGS,
        Operation.TECH_DETECTIONS,
    ]
    assert {o[1] for o in ops} == {"acme.example"}


def test_reason_codes_are_neutral() -> None:
    assert reason_code(SchemaDriftError()) == "schema_drift_error"
    assert reason_code(CreditsExhausted()) == "credits_exhausted"


# payloads ---------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_payloads_round_trip_and_refuse_other_tenants() -> None:
    store = StorePayloads(MemoryPayloadStore(), uuid4())
    c = ctx()
    ref = await store.put(
        c, "company_news", SourceItem("https://acme.example/n", "text", "date_range"), "private"
    )
    assert ref.startswith(f"tenants/{c.tenant_id}/") and "p_" not in ref
    view = await store.view(c.tenant_id, ref)
    assert (view.url, view.visibility, view.match_kind, view.row_key) == (
        "https://acme.example/n",
        "private",
        "date_range",
        "company_news",
    )
    assert (
        await store.put(
            c, "company_news", SourceItem("https://acme.example/n", "text", "date_range"), "private"
        )
        == ref
    )
    with pytest.raises(KeyError):
        await store.view(uuid4(), ref)


# credit -----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prices_come_from_the_cost_table() -> None:
    from account_intelligence.connectors.settings import load_providers_config
    from agents.config import load_skill_config
    from spend.costs import cost_table

    adapter = CreditAdapter(None, None, load_skill_config())  # type: ignore[arg-type]
    table = cost_table(load_providers_config())
    for need in ("web_search", "scrape", "structured_signals", "people_by_title"):
        estimate, known = await adapter.price(need)
        assert estimate >= 0 and isinstance(known, bool)
    assert await adapter.price("teleport") == (0, False)
    items = pull_plan_items(load_skill_config(), table)
    assert items and all((i.provider_code, i.operation) in table for i in items)
    assert all(i.calls == 3 for i in items)  # three phrasings per row (G3)


# delivery ---------------------------------------------------------------------------------------


def test_builder_adapter_reads_member_texts_for_the_name_scan() -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as z:
        z.writestr("word/document.xml", "<w:t>Sourced via a broker</w:t>")
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as z:
        z.writestr("Summary.docx", inner.getvalue())
        z.writestr("notes.md", "Internal Only")
        z.writestr("image.png", b"\x89PNG")
    texts = member_texts(outer.getvalue())
    assert "<w:t>Sourced via a broker</w:t>" in texts and "Internal Only" in texts
    assert "Summary.docx" in texts and not any("PNG" in t for t in texts if t != "image.png")


# tracker ----------------------------------------------------------------------------------------


def test_agent_reasons_map_to_tracker_reason_codes() -> None:
    assert reason_for("consent_required") is ReasonCode.CONSENT_REQUIRED
    assert reason_for("spend_not_approved") is ReasonCode.NO_CAP
    assert reason_for("pull_not_approved") is ReasonCode.PULL_NOT_APPROVED
    assert reason_for("provider_down") is ReasonCode.SOURCE_FAILED
    assert reason_for(None) is None


class _Ledger:
    def __init__(self, records: dict[tuple[UUID, str], CallRecord]) -> None:
        self.records = records

    async def get_call_record(self, tenant_id: UUID, run_id: UUID, call_ref: str) -> CallRecord:
        return self.records[(run_id, call_ref)]


@pytest.mark.asyncio
async def test_inherited_proof_comes_only_from_the_declared_parent() -> None:
    tenant, run, parent, stranger = uuid4(), uuid4(), uuid4(), uuid4()
    rec = CallRecord(
        call_ref="c1", tool_category="web_search", success=True, result_count=1, run_id=str(parent)
    )
    other = CallRecord(
        call_ref="c2", tool_category="web_search", success=True, run_id=str(stranger)
    )
    ledger = InheritingCallLedger(_Ledger({(parent, "c1"): rec, (stranger, "c2"): other}), parent)  # type: ignore[arg-type]
    got = await ledger.get_call_record(tenant, run, "c1")
    assert got.run_id == str(run) and got.result_count == 1
    with pytest.raises(KeyError):
        await ledger.get_call_record(tenant, run, "c2")  # another run's call: refused
    orphan = InheritingCallLedger(_Ledger({}), None)  # type: ignore[arg-type]
    with pytest.raises(KeyError):
        await orphan.get_call_record(tenant, run, "c1")


# grading on Postgres ----------------------------------------------------------------------------


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_mark_conflicting_caps_and_never_raises_a_grade(tenant_a: UUID) -> None:
    from account_intelligence.db.session import tenant_txn
    from account_intelligence.grading import repo
    from account_intelligence.grading.config import load_grading_config
    from agents.production.grading import EngineGrading, InheritedLedger
    from tests.agents.test_pg_adapters import make_run
    from tests.conftest import _make_account
    from tests.grading.db_world import graded_confirmed, graded_lead, run_info

    account = _make_account(tenant_a, f"g-{uuid4().hex[:6]}.example.com")
    run = make_run(tenant_a, account, "full")
    info = run_info(tenant_a, account, run)
    confirmed = await repo.record_graded_fact(tenant_txn, info, graded_confirmed())
    lead = await repo.record_graded_fact(tenant_txn, info, graded_lead())
    engine = EngineGrading(
        tenant_txn,
        InheritedLedger(None, tenant_txn),  # type: ignore[arg-type]
        load_grading_config(),
        None,
    )  # type: ignore[arg-type]
    c = RunContext(tenant_a, run, "g", "G", "g.example.com", False, "sk-1")
    with psycopg.connect(_admin_dsn()) as conn, conn.cursor() as cur:
        cur.execute("UPDATE account_runs SET created_at = %s WHERE id = %s", (info.started_at, run))
        conn.commit()
    new = await engine.mark_conflicting(c, [str(confirmed), str(lead)], "contradiction")
    assert len(new) == 2
    current = {f.fact_id: f for f in await engine.facts(c)}
    assert set(current) == set(new)
    grades = sorted((f.grade, "conflicting" in f.flags) for f in current.values())
    assert grades == [("lead", True), ("probable", True)]  # capped; a Lead never raised
    assert await engine.mark_conflicting(c, list(new), "again") == ()  # flagged: skipped


def _admin_dsn() -> str:
    from tests.conftest import ADMIN_DSN

    return ADMIN_DSN


@pytest.mark.asyncio
async def test_a_web_search_reads_the_accounts_own_pages_in_full(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    seen: list[tuple[str, Any]] = []

    async def fake(route: str, args: Any, cc: Any, rt: Any, *, operation: Any = None) -> Any:
        seen.append((route, args))
        if route == "open_web_search":
            return ConnectorResult(
                status="ok",
                records=(
                    page("https://news.example/acme"),
                    page("https://www.acme.example/about"),
                    page("https://www.acme.example/about"),
                    page("https://ir.acme.example/results"),
                    page("https://jobs.acme.example/slow"),
                ),
                call_refs=("s1",),
            )
        if "ir." in args.url:
            raise SchemaDriftError()  # one unreadable page never loses the search
        if "slow" in args.url:
            raise RetryableProviderError("timeout: ReadTimeout")  # nor a slow one the slice
        return ConnectorResult(status="ok", records=(page(args.url),), call_refs=("r1",))

    monkeypatch.setattr(provider_module, "call_need", fake)
    result = await ConnectorProvider(_Runtime(), uuid4()).call(  # type: ignore[arg-type]
        ctx(), "web_search", "acme results", "company_news"
    )
    reads = [(r, a.url, a.page_kind) for r, a in seen if r != "open_web_search"]
    # only the account's own hosts are read, each once, as a live scrape (a full page read, G5;
    # an extract read may be a cached copy); other hosts stay snippets
    assert reads == [
        ("job_boards_and_pages", "https://www.acme.example/about", "account_page"),
        ("job_boards_and_pages", "https://ir.acme.example/results", "account_page"),
        ("job_boards_and_pages", "https://jobs.acme.example/slow", "account_page"),
    ]
    assert result.call_refs == ("s1", "r1")
