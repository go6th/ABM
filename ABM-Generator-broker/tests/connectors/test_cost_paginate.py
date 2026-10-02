"""Cost model from config, and bounded pagination (with property tests)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from account_intelligence.config import CONFIG_DIR
from account_intelligence.connectors.base import Operation, ParsedPage, ProviderCode
from account_intelligence.connectors.cost import CostModel
from account_intelligence.connectors.errors import ProviderJobTimeout
from account_intelligence.connectors.ledger import args_hash
from account_intelligence.connectors.models import (
    FeedArgs,
    OrgResolveArgs,
    PeopleByTechnologyArgs,
    Provenance,
    ReadPageArgs,
    ScrapeJobPageArgs,
    WebPageRecord,
    WebSearchArgs,
)
from account_intelligence.connectors.paginate import (
    FetchedPage,
    PageLimits,
    PollLimits,
    paginate,
    poll_job,
)
from account_intelligence.connectors.settings import OpCost, ProvidersConfig, load_providers_config
from tests.connectors.conftest import FakeClock, FakeSleep

CFG = load_providers_config()
COST = CostModel(CFG)


# ------------------------------------------------------------------ cost


def test_every_operation_cost_names_a_source_and_unknown_is_null() -> None:
    raw = yaml.safe_load((CONFIG_DIR / "providers.yaml").read_text())
    for code, provider in raw["providers"].items():
        assert provider["rate_limit"]["source"], code
        for op, spec in provider["operations"].items():
            assert spec.get("source"), (code, op)
            if spec["cost_model"] == "unknown":
                assert spec.get("credits") is None and spec.get("usd") is None
                assert spec["source"] == "unknown"
            if spec.get("prd_hint"):
                assert (
                    "verify" in spec["prd_hint"].lower() or "playbook" in spec["prd_hint"].lower()
                )


def test_config_refuses_a_zero_cost_and_an_unknown_with_a_number() -> None:
    with pytest.raises(ValidationError):
        OpCost(cost_model="usd_per_call", usd=0.0, source="x")
    with pytest.raises(ValidationError):
        OpCost(cost_model="unknown", credits=1, source="unknown")
    with pytest.raises(ValidationError):
        OpCost(cost_model="unknown", source="made up")


def test_documented_dollar_costs() -> None:
    e = COST.estimate(
        ProviderCode.P_SEARCH, Operation.WEB_SEARCH, WebSearchArgs(queries=("a b c",))
    )
    assert (e.usd, e.cost_source, e.charged_usd) == (0.005, "documented", 0.005)
    e = COST.estimate(
        ProviderCode.P_SEARCH, Operation.WEB_SEARCH, WebSearchArgs(queries=("a b",), mode="fast")
    )
    assert e.usd == 0.001
    e = COST.estimate(
        ProviderCode.P_SEARCH, Operation.READ_PAGE, ReadPageArgs(url="https://acme.com/a")
    )
    assert e.usd == 0.001


def test_credits_without_a_dollar_rate_are_never_free() -> None:
    reserve = CFG.defaults.unknown_cost_reserve_usd
    e = COST.estimate(
        ProviderCode.P_SCRAPE,
        Operation.SCRAPE_JOB_PAGE,
        ScrapeJobPageArgs(url="https://jobs.acme.com/1", page_kind="job_page"),
    )
    assert (e.credits, e.usd, e.cost_source, e.charged_usd) == (1, None, "credits_only", reserve)
    e = COST.estimate(ProviderCode.P_SIGNALS, Operation.NEWS_EVENTS, FeedArgs(domain="acme.com"))
    assert e.unit == "request" and e.usd is None and e.charged_usd == reserve
    e = COST.estimate(ProviderCode.P_ORG, Operation.ORG_RESOLVE, OrgResolveArgs(domain="acme.com"))
    assert e.credits == 4  # 1 base + employee_count, industry, headquarters_country (paid)
    e = COST.estimate(
        ProviderCode.P_ORG,
        Operation.PEOPLE_BY_TECHNOLOGY,
        PeopleByTechnologyArgs(organization_id=1, technology_slug="python"),
    )
    assert e.credits == 50 * 6  # page limit x (1 base + 5 paid attributes; name is free)


def test_actual_cost_follows_the_documented_billing_rules() -> None:
    scrape = ScrapeJobPageArgs(url="https://jobs.acme.com/1", page_kind="job_page")
    none = COST.actual(
        ProviderCode.P_SCRAPE,
        Operation.SCRAPE_JOB_PAGE,
        scrape,
        provider_credits=None,
        document_returned=False,
    )
    assert none.charged_usd == 0 and none.credits == 0  # billing.md: no document, 0 credits
    org = COST.actual(
        ProviderCode.P_ORG,
        Operation.ORG_RESOLVE,
        OrgResolveArgs(domain="acme.com"),
        provider_credits=73,
        document_returned=True,
    )
    assert org.credits == 73 and org.cost_source == "credits_only" and org.usd is None


def test_a_known_credit_rate_turns_credits_into_dollars() -> None:
    data = CFG.model_dump()
    data["providers"][ProviderCode.P_SCRAPE]["usd_per_credit"] = 0.002
    cost = CostModel(ProvidersConfig.model_validate(data))
    e = cost.estimate(
        ProviderCode.P_SCRAPE,
        Operation.SCRAPE_JOB_PAGE,
        ScrapeJobPageArgs(url="https://jobs.acme.com/1", page_kind="job_page"),
    )
    assert e.usd == pytest.approx(0.002) and e.charged_usd == pytest.approx(0.002)


# ------------------------------------------------------------------ args hash


@given(
    st.dictionaries(
        st.text(min_size=1, max_size=8), st.integers() | st.text(max_size=8), min_size=1, max_size=6
    )
)
def test_property_args_hash_is_stable_under_key_order(d: dict[str, object]) -> None:
    reordered = dict(reversed(list(d.items())))
    assert args_hash("p_x", "op", d) == args_hash("p_x", "op", reordered)


@given(
    st.dictionaries(st.text(min_size=1, max_size=8), st.integers(), min_size=1, max_size=6),
    st.data(),
)
def test_property_args_hash_changes_with_any_argument(
    d: dict[str, int], data: st.DataObject
) -> None:
    key = data.draw(st.sampled_from(sorted(d)))
    changed = {**d, key: d[key] + 1}
    assert args_hash("p_x", "op", d) != args_hash("p_x", "op", changed)
    assert args_hash("p_x", "op", d) != args_hash("p_y", "op", d)
    assert args_hash("p_x", "op", d) != args_hash("p_x", "op2", d)


# ------------------------------------------------------------------ pagination


def _rec(key: str) -> WebPageRecord:
    return WebPageRecord(
        kind="web_page",
        provenance=Provenance(
            provider_code=ProviderCode.P_SEARCH,
            operation=Operation.WEB_SEARCH,
            source_url=None,
            retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            raw_ref="r",
            page=1,
            index=0,
        ),
        lineage_hint=None,
        url=key,
        title_text=None,
        publish_date=None,
        excerpts_text=(),
        full_content_text=None,
    )


def _pages(spec: list[tuple[list[str], str | None]]):
    async def fetch(cursor: str | None, n: int) -> FetchedPage:
        keys, nxt = spec[min(n - 1, len(spec) - 1)]
        body = f"{n}:{keys}:{nxt}".encode()
        return FetchedPage(
            ParsedPage(tuple(_rec(k) for k in keys), len(keys), nxt),
            len(body),
            body.hex(),
            f"ref{n}",
        )

    return fetch


LIMITS = PageLimits(max_pages=5, max_items=100, max_bytes=10_000)


def test_pagination_exhausts_and_dedupes_across_pages() -> None:
    r = asyncio.run(
        paginate(
            _pages([(["a", "b"], "2"), (["b", "c"], None)]), LIMITS, FakeClock(), deadline=None
        )
    )
    assert r.stop_reason == "exhausted" and [x.url for x in r.records] == ["a", "b", "c"]
    assert (r.items_read, r.pages) == (4, 2)


def test_repeating_cursor_is_a_loop_not_an_infinite_loop() -> None:
    r = asyncio.run(
        paginate(
            _pages([(["a"], "2"), (["b"], "2"), (["c"], "2")]), LIMITS, FakeClock(), deadline=None
        )
    )
    assert r.stop_reason == "loop_detected" and not r.complete


def test_identical_page_twice_is_a_loop() -> None:
    async def fetch(cursor: str | None, n: int) -> FetchedPage:
        return FetchedPage(ParsedPage((_rec("a"),), 1, str(n + 1)), 10, "same-digest", f"ref{n}")

    r = asyncio.run(paginate(fetch, LIMITS, FakeClock(), deadline=None))
    assert r.stop_reason == "loop_detected" and r.pages == 2


def test_endless_pagination_stops_at_max_pages_and_is_partial() -> None:
    async def fetch(cursor: str | None, n: int) -> FetchedPage:
        return FetchedPage(ParsedPage((_rec(f"k{n}"),), 1, str(n + 1)), 10, f"d{n}", f"ref{n}")

    r = asyncio.run(paginate(fetch, LIMITS, FakeClock(), deadline=None))
    assert (r.stop_reason, r.pages, r.complete) == ("max_pages", 5, False)


def test_max_items_max_bytes_and_deadline() -> None:
    clock = FakeClock()
    pages = _pages([([f"x{i}" for i in range(10)], "2"), ([f"y{i}" for i in range(10)], None)])
    r = asyncio.run(paginate(pages, PageLimits(5, 15, 10_000), clock, deadline=None))
    assert r.stop_reason == "max_items" and len(r.records) == 15
    r = asyncio.run(paginate(pages, PageLimits(5, 100, 5), clock, deadline=None))
    assert r.stop_reason == "max_bytes"

    async def slow(cursor: str | None, n: int) -> FetchedPage:
        clock.advance(100)
        return FetchedPage(ParsedPage((_rec(f"s{n}"),), 1, str(n + 1)), 1, f"s{n}", "r")

    r = asyncio.run(paginate(slow, LIMITS, clock, deadline=clock.monotonic() + 150))
    assert r.stop_reason == "deadline" and r.pages == 2


@settings(max_examples=200, deadline=None)
@given(
    pages=st.lists(
        st.tuples(
            st.lists(st.sampled_from("abcdefgh"), max_size=5),
            st.none() | st.sampled_from(["1", "2", "3", "4", "5", "6"]),
        ),
        min_size=1,
        max_size=30,
    ),
    max_pages=st.integers(1, 10),
    max_items=st.integers(1, 20),
)
def test_property_pagination_always_terminates_within_caps(
    pages, max_pages: int, max_items: int
) -> None:
    r = asyncio.run(
        paginate(
            _pages(pages), PageLimits(max_pages, max_items, 10_000), FakeClock(), deadline=None
        )
    )
    assert r.pages <= max_pages
    assert len(r.records) <= max_items
    assert len({x.url for x in r.records}) == len(r.records)  # deduped
    assert r.stop_reason in {"exhausted", "max_pages", "max_items", "max_bytes", "loop_detected"}


# ------------------------------------------------------------------ provider jobs


def test_job_polling_is_bounded_and_cancels_on_timeout() -> None:
    import random

    clock = FakeClock()
    sleep = FakeSleep(clock)
    cancelled: list[bool] = []

    async def never() -> ParsedPage | None:
        return None

    async def cancel() -> None:
        cancelled.append(True)

    with pytest.raises(ProviderJobTimeout):
        asyncio.run(
            poll_job(
                never,
                PollLimits(3, 1, 40, 20),
                clock,
                sleep,
                random.Random(1),
                deadline=None,
                cancel=cancel,
            )
        )
    assert cancelled == [True] and sum(sleep.calls) < 20 and len(sleep.calls) <= 40
