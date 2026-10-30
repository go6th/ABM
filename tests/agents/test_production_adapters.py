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
async def test_the_pages_a_search_found_are_read_side_by_side(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """With the caps off every page a search returned is read; the reads overlap instead of
    waiting on each other (speed audit 2026-10-27), and a page that cannot be read is skipped
    while the others still arrive in the search's order."""
    import asyncio
    import time

    from account_intelligence.connectors.errors import ConnectorError

    delay = 0.05
    own = [f"https://acme.example/p{i}" for i in range(6)]
    off = [f"https://news{i}.example/a" for i in range(6)]
    reads: list[str] = []

    async def fake(route: str, args: Any, cc: Any, rt: Any, *, operation: Any = None) -> Any:
        if route == "open_web_search":
            return ConnectorResult(
                status="ok", records=tuple(page(u) for u in (*off, *own)), call_refs=("s1",)
            )
        await asyncio.sleep(delay)
        reads.append(args.url)
        if args.url.endswith("p3") or args.url.startswith("https://news2"):
            raise ConnectorError("unreadable")
        return ConnectorResult(status="ok", records=(page(args.url),), call_refs=(args.url,))

    monkeypatch.setattr(provider_module, "call_need", fake)
    monkeypatch.setenv("RESEARCH_CAPS", "off")
    started = time.monotonic()
    result = await ConnectorProvider(_Runtime(), uuid4()).call(  # type: ignore[arg-type]
        ctx(), "web_search", "acme cio", "key_contact_profiles_posts"
    )
    elapsed = time.monotonic() - started
    assert len(reads) == 12  # a row without an own-site search: each page read once
    # one after another the reads alone take 12 x delay; four at a time they take about a
    # quarter of that
    assert elapsed < 12 * delay * 0.6, elapsed
    got = [r for r in result.call_refs if r != "s1"]
    assert got == [
        u for u in (*own, *off) if u not in ("https://acme.example/p3", "https://news2.example/a")
    ]


@pytest.mark.asyncio
async def test_feeds_use_the_row_operation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ops: list[Any] = []

    async def fake(route: str, args: Any, cc: Any, rt: Any, *, operation: Any = None) -> Any:
        if route == "news_tech_job_feeds":  # the news row adds an own-site newsroom search
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


class _Archive:
    """A link archive that keeps what it was asked to record (the structured copies it stores
    are kept by key)."""

    def __init__(self) -> None:
        self.entries: list[Any] = []
        self.records: dict[str, dict[str, Any]] = {}

    def now(self) -> datetime:
        return T0

    async def record(self, entries: Any) -> int:
        self.entries.extend(entries)
        return len(self.entries)

    async def entry_for_record(
        self,
        url: str,
        kind: str,
        record: dict[str, Any],
        *,
        title: str | None = None,
        archived_at: datetime | None = None,
        source_ref: str | None = None,
    ) -> Any:
        from account_intelligence.archive import ArchiveEntry
        from account_intelligence.archive_records import identity_bytes

        key = f"archive/{abs(hash(identity_bytes(record)))}"
        self.records[key] = record
        return ArchiveEntry(
            url, kind, key, archived_at or T0, "json", title=title, source_ref=source_ref
        )


@pytest.mark.asyncio
async def test_public_items_are_archived_under_their_address_and_private_ones_are_not() -> None:
    archive = _Archive()
    store = StorePayloads(MemoryPayloadStore(), uuid4(), archive=archive)  # type: ignore[arg-type]
    c = ctx()
    text = "Jane Doe - CIO - Acme | LinkedIn\nA search result."
    await store.put(
        c,
        "key_contact_profiles_posts",
        SourceItem("https://www.linkedin.com/in/jane-doe", text),
        "public",
    )
    await store.put(
        c, "company_news", SourceItem("https://acme.example/n", "feed", "date_range"), "public"
    )
    news_meta = (
        ("record", "news"),
        ("title", "Acme names a new CIO"),
        ("summary", "Acme appointed a CIO."),
        ("published", "2026-09-30T08:00:00Z"),
        ("category", "leadership_change"),
    )
    await store.put(
        c,
        "company_news",
        SourceItem(
            "https://www.news.example/a/1", "Acme names a new CIO\nThe sentence.", None, news_meta
        ),
        "public",
    )
    await store.put(
        c, "prior_customer_status", SourceItem("https://mail.example/1", "private"), "private"
    )
    assert [(e.url, e.kind, e.title, e.archived_at) for e in archive.entries] == [
        ("https://www.linkedin.com/in/jane-doe", "profile", "Jane Doe - CIO - Acme | LinkedIn", T0),
        ("https://acme.example/n", "feed_item", "feed", T0),
        ("https://www.news.example/a/1", "page", "Acme names a new CIO", T0),
    ]
    profile, feed, news = (archive.records[e.payload_ref] for e in archive.entries)
    assert profile["schema"] == "page-archive/1" and profile["text"] == text
    assert feed["schema"] == "page-archive/2" and feed["kind"] == "feed_item"
    # a news item becomes the cleaned news record: headline, publisher, date, category, summary,
    # the article without its headline line, and the source classified
    assert news["schema"] == "news-archive/2"
    assert (news["title"], news["publisher"], news["published_date"]) == (
        "Acme names a new CIO",
        "news.example",
        "2026-09-30",
    )
    assert (news["category"], news["summary"]) == ("leadership change", "Acme appointed a CIO.")
    assert news["body_markdown"] == "The sentence." and "article_text" not in news
    assert (news["collected_at"], news["source"]) == (T0.isoformat(), "6th agents")
    assert (news["source_type"], news["is_primary"], news["fact_grade"]) == ("Other", False, "Lead")
    # every copy keeps the reference of the stored item it was made from (audit only)
    assert all(str(e.source_ref).startswith("tenants/") for e in archive.entries)


@pytest.mark.asyncio
async def test_a_field_the_guard_refuses_is_blank_in_the_archived_copy() -> None:
    from account_intelligence.archive_records import page_record

    record = page_record(
        "https://x.example/p",
        "The leaky sentence.",
        title="A page",
        captured_at=T0,
        guard=lambda t: (t, "leaky" in t),
    )
    assert record["text"] == "" and record["title"] == "A page" and record["withheld"] == ["text"]


def test_items_of_carries_what_a_record_states_about_itself() -> None:
    from account_intelligence.connectors.models import NewsEventRecord

    provenance = prov("https://n.example/a", Operation.NEWS_EVENTS)
    news = NewsEventRecord(
        provenance=provenance,
        kind="news_event",
        lineage_hint=None,
        event_id="e1",
        category="funding",
        summary_text="Raised.",
        found_at="2026-10-01T00:00:00Z",
        confidence=0.9,
        article_sentence_text="Acme raised money.",
        effective_date=None,
        amount_normalized_usd=None,
        planning=False,
        article_url="https://n.example/a",
        article_title_text="Acme raises",
        article_published_at="2026-09-30",
    )
    (item,) = items_of((news,))
    assert dict(item.meta) == {
        "record": "news",
        "title": "Acme raises",
        "summary": "Raised.",
        "published": "2026-09-30",
        "category": "funding",
    }


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
    # podcasts is not an own-site row: one open search (own-site rows add a second search and
    # its reads, tests/agents/test_research_core.py)
    result = await ConnectorProvider(_Runtime(), uuid4()).call(  # type: ignore[arg-type]
        ctx(), "web_search", "acme results", "podcasts"
    )
    reads = [(r, a.url, a.page_kind) for r, a in seen if r == "job_boards_and_pages"]
    # the account's own hosts are read, each once, as a live scrape (a full page read, G5; an
    # extract read may be a cached copy)
    assert reads == [
        ("job_boards_and_pages", "https://www.acme.example/about", "account_page"),
        ("job_boards_and_pages", "https://ir.acme.example/results", "account_page"),
        ("job_boards_and_pages", "https://jobs.acme.example/slow", "account_page"),
    ]
    # and the other sites' pages it found are read in full too (owner decision 2026-10-21)
    off_site = [(r, a.url) for r, a in seen if r == "read_known_page"]
    assert off_site == [("read_known_page", "https://news.example/acme")]
    assert result.call_refs == ("s1", "r1", "r1")


@pytest.mark.asyncio
async def test_an_off_site_read_opens_only_the_page_the_search_returned(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    allowed: list[frozenset[str]] = []
    opened: list[str] = []

    async def fake(route: str, args: Any, cc: Any, rt: Any, *, operation: Any = None) -> Any:
        if route == "open_web_search":
            return ConnectorResult(
                status="ok",
                records=(
                    page("https://www.linkedin.com/posts/acme"),  # a sign-in wall: not opened
                    page("https://news.example/a"),
                    page("https://news.example/a"),
                    page("https://trade.example/b"),
                    page("https://third.example/c"),  # past the per-search limit
                ),
                call_refs=("s1",),
            )
        opened.append(args.url)
        allowed.append(cc.extra_allowed_hosts)
        long = page(args.url).model_copy(update={"full_content_text": "x" * 100_000})
        return ConnectorResult(
            status="ok", records=(long,), call_refs=("r",), operation=Operation.READ_PAGE
        )

    monkeypatch.setattr(provider_module, "call_need", fake)
    result = await ConnectorProvider(_Runtime(), uuid4()).call(  # type: ignore[arg-type]
        ctx(), "web_search", "acme results", "company_news"
    )
    assert opened == ["https://news.example/a", "https://trade.example/b"]
    # each read allows its own host only, never the others or a lasting allow-list
    assert allowed == [frozenset({"news.example"}), frozenset({"trade.example"})]
    reads = [i for i in result.items if "news.example" in i.url or "trade.example" in i.url]
    assert reads and all(len(i.text) <= provider_module.MAX_READ_CHARS for i in reads)
