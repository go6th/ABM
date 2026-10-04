"""Production adapters (spec 8): connector error mapping, the people-source switch, ledger costs,
source codes, tracker hand-off and the workflow stage on real Postgres."""

from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.connectors.base import ConnectorResult, Counts, Operation, ProviderCode
from account_intelligence.connectors.errors import (
    BudgetExceeded,
    ConnectorError,
    NotSupportedYet,
    RateLimited,
)
from account_intelligence.connectors.models import OrganizationRecord, PersonRecord, Provenance
from people import adapters
from people.config import load_people_config
from people.errors import NotConfigured
from people.ports import SourceUnavailable

DOMAIN = "synthetic-acme.example.com"
ALL_OPS = frozenset(
    {"people_by_title", "people_by_technology", "org_resolve", "profile_enrichment"}
)


def _prov(op: Operation, index: int = 0) -> Provenance:
    return Provenance(
        provider_code=ProviderCode.P_PEOPLE,
        operation=op,
        source_url=None,
        retrieved_at=datetime(2026, 10, 3, tzinfo=UTC),
        raw_ref="raw/abc",
        page=1,
        index=index,
    )


def _person(index: int, url: str | None) -> PersonRecord:
    return PersonRecord(
        kind="person",
        provenance=_prov(Operation.PEOPLE_BY_TITLE, index),
        lineage_hint=None,
        provider_person_id=f"id{index}",
        profile_url=url,
        name_text=f"Synthetic {index}",
        job_title_text="Director, Salesforce",
        job_function=None,
        job_level=None,
        location=None,
    )


def _err(error: Exception, ref: str | None) -> Exception:
    error.call_ref = ref  # type: ignore[attr-defined]  # set by call_operation in production
    return error


def _deps(**kw: Any) -> Any:
    base = {
        "connectors": object(),
        "domain": DOMAIN,
        "retry_max_attempts": 3,
        "call_context": lambda: None,
    }
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    seen: list[Any] = []
    replies: dict[Operation, Any] = {}

    async def fake(op: Operation, args: Any, ctx: Any, runtime: Any) -> Any:
        seen.append((op, args))
        reply = replies[op]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(adapters, "call_operation", fake)
    seen.append(replies)  # the test sets replies through calls[0]
    return seen


@pytest.mark.asyncio
async def test_title_search_maps_person_records(calls: list[Any]) -> None:
    replies = calls[0]
    replies[Operation.PEOPLE_BY_TITLE] = ConnectorResult(
        status="done",
        records=(_person(0, "https://www.linkedin.com/in/a"), _person(1, None)),
        call_refs=("ref1",),
        counts=Counts(read=2, matched=2, pages=1),
    )
    search = adapters.ConnectorPeopleSearch(_deps(), ALL_OPS, load_people_config())
    page = await search.search_titles("salesforce", 1, ("Salesforce Director",), None)
    assert [r.source_record_id for r in page.records] == ["raw/abc#1.0", "raw/abc#1.1"]
    assert page.records[0].search_domain == DOMAIN and page.call_ref == "ref1"
    assert page.next_cursor is None and page.reported_count == 2
    assert (await search.search_titles("salesforce", 1, ("x",), "more")).records == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "reason", "ref"),
    [
        (
            NotSupportedYet(provider_code="p_people", operation="people_by_title"),
            "not_available_yet",
            None,
        ),
        (_err(BudgetExceeded(), "b1"), "budget_cap", "b1"),
        (_err(ConnectorError(), "c1"), "source_unavailable", "c1"),
        (_err(RateLimited(attempt=3), "r1"), "source_unavailable", "r1"),
    ],
)
async def test_connector_errors_map_to_typed_unavailability(
    calls: list[Any], error: Exception, reason: str, ref: str | None
) -> None:
    calls[0][Operation.PEOPLE_BY_TITLE] = error
    search = adapters.ConnectorPeopleSearch(_deps(), ALL_OPS, load_people_config())
    with pytest.raises(SourceUnavailable) as caught:
        await search.search_titles("salesforce", 1, ("x",), None)
    assert (caught.value.reason, caught.value.call_ref) == (reason, ref)


@pytest.mark.asyncio
async def test_retryable_below_the_node_budget_is_reraised(calls: list[Any]) -> None:
    calls[0][Operation.PEOPLE_BY_TITLE] = _err(RateLimited(attempt=1), "r1")
    search = adapters.ConnectorPeopleSearch(_deps(), ALL_OPS, load_people_config())
    with pytest.raises(RateLimited):  # the node RetryPolicy owns the retry (W6)
        await search.search_titles("salesforce", 1, ("x",), None)


@pytest.mark.asyncio
async def test_source_switch_and_missing_runtime_mean_not_available_yet(calls: list[Any]) -> None:
    cfg = load_people_config()
    for deps, ops in (
        (_deps(connectors=None), ALL_OPS),
        (_deps(), frozenset({"people_by_technology"})),
    ):
        with pytest.raises(SourceUnavailable) as caught:
            await adapters.ConnectorPeopleSearch(deps, ops, cfg).search_titles(
                "sap", 1, ("x",), None
            )
        assert caught.value.reason == "not_available_yet" and caught.value.call_ref is None
    assert len(calls) == 1  # no connector call was made


@pytest.mark.asyncio
async def test_association_needs_a_technology_id_then_resolves_the_org(calls: list[Any]) -> None:
    from dataclasses import replace

    cfg = load_people_config()
    search = adapters.ConnectorPeopleSearch(_deps(), ALL_OPS, cfg)
    with pytest.raises(SourceUnavailable) as caught:
        await search.search_association("salesforce", ("IT",), None)
    assert caught.value.reason == "not_available_yet"
    configured = adapters.ConnectorPeopleSearch(
        _deps(), ALL_OPS, replace(cfg, technology_by_lane={"salesforce": "salesforce"})
    )
    org = OrganizationRecord(
        kind="organization",
        provenance=_prov(Operation.ORG_RESOLVE),
        lineage_hint=None,
        input_domain=DOMAIN,
        matched=True,
        organization_id=7,
        slug=None,
        name=None,
        url=None,
        employee_count=None,
        industry=None,
        headquarters_country=None,
    )
    calls[0][Operation.ORG_RESOLVE] = ConnectorResult(
        status="done", records=(org,), call_refs=("o1",)
    )
    calls[0][Operation.PEOPLE_BY_TECHNOLOGY] = ConnectorResult(
        status="partial", records=(_person(0, "https://www.linkedin.com/in/z"),), call_refs=("t1",)
    )
    page = await configured.search_association("salesforce", ("IT",), None)
    assert page.records[0].origin == "association" and not page.complete
    assert calls[-1][1].organization_id == 7
    assert (await configured.search_association("salesforce", ("IT",), "next")).records == ()
    calls[0][Operation.ORG_RESOLVE] = ConnectorResult(status="done", records=(), call_refs=("o2",))
    with pytest.raises(SourceUnavailable) as caught:
        await configured.search_association("salesforce", ("IT",), None)
    assert (caught.value.reason, caught.value.call_ref) == ("source_unavailable", "o2")


@pytest.mark.asyncio
async def test_unavailable_ports_and_run_input() -> None:
    with pytest.raises(SourceUnavailable):
        await adapters.UnavailableProfiles().pull("https://www.linkedin.com/in/a")
    with pytest.raises(SourceUnavailable):
        await adapters.UnavailablePosts().pull("https://www.linkedin.com/in/a", date(2026, 1, 1))
    with pytest.raises(NotConfigured):
        await adapters.NoMentions().mentions()
    with pytest.raises(SourceUnavailable):
        await adapters.NoMentions().resolve("Synthetic", 0)
    run = adapters.RunPeopleInput(
        {
            "lanes_off": ["veeva"],
            "people_regions": ["Germany"],
            "pursuing_client": "Synthetic Client",
            "reference_employers": ["Other Synthetic"],
        },
        date(2026, 10, 3),
        0.6,
    )
    assert await run.lanes_off() == {"veeva"} and await run.regions() == ("Germany",)
    assert await run.reference_employers() == ("Synthetic Client", "Other Synthetic")
    assert await run.as_of() == date(2026, 10, 3) and await run.people_budget_usd() == 0.6
    assert await adapters.RunPeopleInput({}, date(2026, 10, 3), None).reference_employers() is None


@pytest.mark.asyncio
async def test_proposal_sink_and_extractor() -> None:
    stored: list[Any] = []

    class Store:
        async def put_stage(self, stage: str, items: list[Any]) -> str:
            stored.append((stage, items))
            return "ref"

    sink = adapters.PeopleProposalSink(Store())
    await sink.submit_claims([])
    assert sink.reference is None
    from people.claims import title_claims
    from tests.people.conftest import person

    claims, _ = title_claims([person("pabc", title="Director, SAP")])
    await sink.submit_claims(claims)
    assert sink.reference == "ref" and stored[0][0] == "people"

    class Llm:
        def __init__(self, text: str) -> None:
            self.text, self.calls = text, []

        async def complete(self, tier: str, system: str, user: str, *, max_tokens: int) -> Any:
            self.calls.append((tier, system))
            return SimpleNamespace(text=self.text)

    llm = Llm('{"person_key": "k", "findings": []}')
    out = await adapters.LlmPeopleExtractor(SimpleNamespace(llm=llm))("k", "text", "CANARY-x")
    assert out == {"person_key": "k", "findings": []} and "CANARY-x" in llm.calls[0][1]
    assert llm.calls[0][0] == "extractor"
    raw = await adapters.LlmPeopleExtractor(SimpleNamespace(llm=Llm("not json")))("k", "t", "c")
    assert raw == "not json"


# ---- on real Postgres --------------------------------------------------------------------------


def _run_deps(w: Any, connectors: Any = None) -> Any:
    from account_intelligence.db.session import tenant_txn
    from account_intelligence.platform.hermetic import MemoryPayloadStore
    from account_intelligence.workflow.deps import RunDeps
    from account_intelligence.workflow.spec import load_spec

    return RunDeps(
        tenant_id=w.tenant,
        run_id=w.run,
        account_id=w.account,
        domain=w.domain,
        txn=tenant_txn,
        llm=SimpleNamespace(),
        payloads=MemoryPayloadStore(),
        clock=SimpleNamespace(),
        spec=load_spec(),
        connectors=connectors,
    )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_ledger_costs_source_codes_and_tracker_hand_off() -> None:
    from tests.tracker.world import fetch, ledger_done, make_world, txn

    w = make_world()
    async with txn(w) as t:
        await t.seed(w.run, w.tenant)
    deps = _run_deps(w)
    ref = ledger_done(w, operation="people_by_title")
    assert await adapters.LedgerCosts(deps).cost_usd(ref) is None  # unknown is never 0
    assert await adapters.LedgerCosts(deps).cost_usd("no-such-ref") is None
    code = await adapters.SourceRegistry(deps).code_for("https://www.linkedin.com/in/synthetic-a")
    again = await adapters.SourceRegistry(deps).code_for("https://www.linkedin.com/in/synthetic-a")
    assert code == again and code.startswith("S-")
    evidence = adapters.PeopleTrackerEvidence(deps)
    await evidence.record(
        "people_by_platform",
        [],
        expected_calls=1,
        cap_hit=False,
        not_attempted_reason="not_available_yet",
    )
    await evidence.record(
        "posts_priority_people", [], expected_calls=1, cap_hit=False, not_attempted_reason=None
    )
    await evidence.record(
        "full_profiles_senior_people",
        [ref],
        expected_calls=1,
        cap_hit=False,
        not_attempted_reason=None,
    )
    rows = dict(
        fetch(
            "SELECT row_key, status FROM coverage_rows WHERE run_id = %s AND row_key IN"
            " ('people_by_platform', 'posts_priority_people', 'full_profiles_senior_people')",
            w.run,
        )
    )
    assert rows == {
        "people_by_platform": "not_done",
        "posts_priority_people": "not_done",
        "full_profiles_senior_people": "done",
    }


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_workflow_people_stage_without_providers_is_honest_and_replays() -> None:
    from account_intelligence.workflow.stages.people import people_stage
    from tests.tracker.world import fetch, make_world, txn

    w = make_world()
    async with txn(w) as t:
        await t.seed(w.run, w.tenant)
    deps = _run_deps(w)
    first = await people_stage(deps, {})
    for row in ("people_by_platform", "full_profiles_senior_people", "posts_priority_people"):
        assert first["coverage_reports"][row] == {"status": "not_done"}
    reasons = dict(
        fetch(
            "SELECT row_key, reason_code FROM coverage_rows WHERE run_id = %s AND row_key"
            " IN ('people_by_platform', 'full_profiles_senior_people')",
            w.run,
        )
    )
    assert reasons == {
        "people_by_platform": "not_available_yet",
        "full_profiles_senior_people": "not_planned",
    }
    assert fetch("SELECT count(*) FROM people_pipeline_runs WHERE run_id = %s", w.run)[0][0] == 1
    called = {"n": 0}

    async def research(state: Any) -> dict[str, Any]:
        called["n"] += 1
        return {
            "coverage_reports": {"employee_content_github": {"status": "not_done"}},
            "counters": {"tool_calls": 2},
        }

    again = await people_stage(deps, {}, research)
    assert called["n"] == 1 and again["counters"]["tool_calls"] == 2
    assert again["coverage_reports"]["employee_content_github"] == {"status": "not_done"}
    assert fetch("SELECT count(*) FROM people_pipeline_runs WHERE run_id = %s", w.run)[0][0] == 1
    _ = uuid4()
