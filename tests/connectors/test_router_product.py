"""Routing and fallback, adapter rules, failure-to-product mapping, leak scanning."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from uuid import UUID

import pytest

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.errors import (
    NonRetryableProviderError,
    PolicyRefused,
    RetryableProviderError,
    SchemaDriftError,
    SecurityError,
)
from account_intelligence.connectors.models import (
    FeedArgs,
    PeopleByTechnologyArgs,
    PeopleByTitleArgs,
    ProfileEnrichmentArgs,
    ScrapeJobPageArgs,
    WebSearchArgs,
)
from account_intelligence.connectors.registry_internal import vendor_names
from account_intelligence.connectors.router import Sink, assert_sink_allowed, call_need
from account_intelligence.connectors.settings import load_providers_config
from tests.connectors.conftest import Harness, Reply, context, ledger_rows, make_run


def allow_fallback(tenant: UUID, allowed: bool = True) -> None:
    """The tenant policy's say on a fallback when credits run out (D-31 key; D-47 keeps it)."""
    import psycopg

    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        c.execute(
            "UPDATE tenants SET policy_json = policy_json || jsonb_build_object("
            "'allow_fallback_on_exhaustion', %s::boolean) WHERE id = %s",
            (allowed, tenant),
        )


REPO = Path(__file__).resolve().parents[2]
DENYLIST = REPO / ".claude/skills/account-intelligence-platform/assets/client_output_denylist.txt"


def _denylist() -> list[re.Pattern[str]]:
    lines = [ln.strip() for ln in DENYLIST.read_text().splitlines()]
    return [re.compile(ln, re.I) for ln in lines if ln and not ln.startswith("#")]


def _leaks(text: str) -> list[str]:
    hits = [p.pattern for p in _denylist() if p.search(text)]
    hits += [n for n in vendor_names(load_providers_config()).values() if n.lower() in text.lower()]
    return hits


# ------------------------------------------------------------------ routing (database)


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_fallback_on_empty_balance_is_taken_and_recorded(
    harness: Harness, tenant_a: UUID
) -> None:
    allow_fallback(tenant_a)
    account, run, domain = make_run(tenant_a, domain="acme.com")
    ctx = context(tenant_a, account, run, domain)
    harness.providers[ProviderCode.P_SCRAPE].script(Reply(status=402, body={"success": False}))
    harness.providers[ProviderCode.P_SEARCH].script(Reply.cassette("p_search", "read_page"))
    result = await call_need(
        "job_boards_and_pages",
        ScrapeJobPageArgs(url="https://jobs.acme.com/1", page_kind="job_page"),
        ctx,
        harness.runtime(),
    )
    assert result.status == "done" and result.provider_code is ProviderCode.P_SEARCH
    assert result.fell_back_from is ProviderCode.P_SCRAPE and result.route == "job_boards_and_pages"
    row = ledger_rows(run)[0]
    assert (row["route"], row["fell_back_from"], row["provider_code"]) == (
        "job_boards_and_pages",
        "p_scrape",
        "p_search",
    )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_d47_default_takes_the_alternate_when_the_tenant_policy_is_silent(
    harness: Harness, tenant_a: UUID
) -> None:
    """D-47 (proposal, supersedes the D-31 default; conflict C16 recorded): with no tenant policy
    the exhaustion ladder's second rung, an allowed alternate route, is taken and labelled."""
    account, run, domain = make_run(tenant_a, domain="acme.com")
    ctx = context(tenant_a, account, run, domain)
    harness.providers[ProviderCode.P_SCRAPE].script(Reply(status=402, body={"success": False}))
    harness.providers[ProviderCode.P_SEARCH].script(Reply.cassette("p_search", "read_page"))
    result = await call_need(
        "job_boards_and_pages",
        ScrapeJobPageArgs(url="https://jobs.acme.com/1", page_kind="job_page"),
        ctx,
        harness.runtime(),
    )
    assert result.status == "done" and result.provider_code is ProviderCode.P_SEARCH
    assert result.fell_back_from is ProviderCode.P_SCRAPE


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_exhaustion_does_not_fall_back_unless_the_tenant_allows_it(
    harness: Harness, tenant_a: UUID
) -> None:
    """A tenant whose policy refuses a fallback: credits exhausted makes the need Blocked and no
    weaker source is called. (D-31 made this the default; D-47 makes the alternate the default,
    so the refusal is now the tenant's explicit choice: see the D-47 test below.)"""
    allow_fallback(tenant_a, False)
    account, run, domain = make_run(tenant_a, domain="acme.com")
    ctx = context(tenant_a, account, run, domain)
    harness.providers[ProviderCode.P_SCRAPE].script(Reply(status=402, body={"success": False}))
    result = await call_need(
        "job_boards_and_pages",
        ScrapeJobPageArgs(url="https://jobs.acme.com/1", page_kind="job_page"),
        ctx,
        harness.runtime(),
    )
    assert result.status == "blocked"
    assert "no fallback on exhaustion" in (result.blocked_reason or "")
    assert harness.providers[ProviderCode.P_SEARCH].calls == 0  # the fallback was never called
    assert not any(r["provider_code"] == "p_search" for r in ledger_rows(run))


@pytest.mark.requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (Reply(body={"success": True}), SchemaDriftError),  # data missing: drift, no fallback
        (Reply(status=400, body={}), NonRetryableProviderError),
        (Reply(status=503, body={}), RetryableProviderError),  # node retries; not a fallback
    ],
)
async def test_no_fallback_on_drift_rejection_or_a_single_outage(
    harness: Harness, tenant_a: UUID, reply: Reply, error: type[Exception]
) -> None:
    account, run, domain = make_run(tenant_a, domain="acme.com")
    ctx = context(tenant_a, account, run, domain)
    harness.providers[ProviderCode.P_SCRAPE].script(reply)
    with pytest.raises(error):
        await call_need(
            "job_boards_and_pages",
            ScrapeJobPageArgs(url="https://jobs.acme.com/1", page_kind="job_page"),
            ctx,
            harness.runtime(),
        )
    assert harness.providers[ProviderCode.P_SEARCH].calls == 0


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_security_refusal_never_falls_back(harness: Harness, tenant_a: UUID) -> None:
    account, run, domain = make_run(tenant_a, domain="acme.com")
    with pytest.raises(SecurityError):
        await call_need(
            "job_boards_and_pages",
            ScrapeJobPageArgs(url="https://evil.com/1", page_kind="job_page"),
            context(tenant_a, account, run, domain),
            harness.runtime(),
        )
    assert all(p.calls == 0 for p in harness.providers.values())


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_outage_with_chat_only_fallback_is_blocked_with_a_reason(
    harness: Harness, tenant_a: UUID
) -> None:
    account, run, domain = make_run(tenant_a)
    ctx = context(tenant_a, account, run, domain)
    rt = harness.runtime()
    for _ in range(rt.config.defaults.breaker.failure_threshold):
        await rt.breaker.record_failure(ProviderCode.P_SEARCH)
    result = await call_need("open_web_search", WebSearchArgs(queries=("acme news",)), ctx, rt)
    assert result.status == "blocked" and result.records == ()
    assert "circuit open" in (result.blocked_reason or "") and "chat-only" in (
        result.blocked_reason or ""
    )


@pytest.mark.requires_postgres
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("need", "args", "operation", "fragment"),
    [
        (
            "people_by_title",
            PeopleByTitleArgs(domain="acme.com", titles=("CTO",)),
            None,
            "planner-issued",
        ),
        (
            "people_by_technology",
            PeopleByTechnologyArgs(organization_id=1, technology_slug="python"),
            None,
            "quota exhausted",
        ),
        (
            "profiles_posts_comments",
            ProfileEnrichmentArgs(profile_url="https://acme.com/in/x"),
            None,
            "no fallback",
        ),
        ("client_history", FeedArgs(domain="acme.com"), None, "not part of the broker layer"),
        (
            "news_tech_job_feeds",
            FeedArgs(domain="acme.com"),
            Operation.NEWS_EVENTS,
            "planner-issued",
        ),
    ],
)
async def test_routes_without_an_available_provider_are_blocked(
    harness: Harness,
    tenant_a: UUID,
    need: str,
    args,
    operation,
    fragment: str,  # type: ignore[no-untyped-def]
) -> None:
    allow_fallback(tenant_a)  # the fallback branches are under test here
    account, run, domain = make_run(tenant_a, domain="acme.com")
    ctx = context(tenant_a, account, run, domain)
    harness.providers[ProviderCode.P_ORG].fallback = Reply(status=402, body={})
    harness.providers[ProviderCode.P_SIGNALS].fallback = Reply(status=402, body={})
    result = await call_need(need, args, ctx, harness.runtime(), operation=operation)
    assert result.status == "blocked", result
    assert fragment in (result.blocked_reason or ""), result.blocked_reason
    assert not _leaks(result.blocked_reason or "")


# ------------------------------------------------------------------ adapter rules


def test_provider_output_never_goes_to_a_hosted_file_or_p_org_to_clients() -> None:
    for code in ProviderCode:
        with pytest.raises(PolicyRefused):
            assert_sink_allowed(code, Sink.HOSTED_FILE)
        assert_sink_allowed(code, Sink.INTERNAL_RAW)
    with pytest.raises(PolicyRefused):
        assert_sink_allowed(ProviderCode.P_ORG, Sink.CLIENT_EXPORT)


def test_routes_match_the_skill_playbook() -> None:
    import yaml

    playbook = yaml.safe_load(
        (REPO / ".claude/skills/account-intelligence-platform/assets/routing.yaml").read_text()
    )
    ours = {r.need: r for r in load_providers_config().routes}
    assert set(ours) == {r["need"] for r in playbook["routes"]}
    for r in playbook["routes"]:
        route = ours[r["need"]]
        first = route.first.provider or route.first.builtin
        assert first == r["first"]
        fb = route.fallback.provider or route.fallback.builtin if route.fallback else None
        assert fb == r["fallback"]


# ------------------------------------------------------------------ failure to product
# The starter graph is retired. Its node decision and its outage-to-PARTIAL proof moved to the
# Workflow v1 path: tests/workflow/test_calls.py (retry ownership per attempt) and
# tests/system/test_failure_injection.py (outage and drift give Blocked rows, the run is PARTIAL).


# ------------------------------------------------------------------ leak scanning


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_no_vendor_name_in_keys_logs_errors_or_results(
    harness: Harness, tenant_a: UUID, caplog
) -> None:  # type: ignore[no-untyped-def]
    from account_intelligence.connectors.service import call_operation

    caplog.set_level(logging.DEBUG, logger="account_intelligence.connectors")
    account, run, domain = make_run(tenant_a, domain="acme.com")
    ctx = context(tenant_a, account, run, domain)
    rt = harness.runtime()
    harness.providers[ProviderCode.P_SEARCH].script(
        Reply.cassette("p_search", "web_search"), Reply(status=500, body={})
    )
    result = await call_operation(
        Operation.WEB_SEARCH, WebSearchArgs(queries=("acme news",)), ctx, rt
    )
    messages: list[str] = []
    for exc_type, call in [
        (
            RetryableProviderError,
            lambda: call_operation(Operation.WEB_SEARCH, WebSearchArgs(queries=("x y",)), ctx, rt),
        ),
        (
            SecurityError,
            lambda: call_need(
                "read_known_page",
                ScrapeJobPageArgs(url="https://x.org/", page_kind="job_page"),
                ctx,
                rt,
            ),
        ),
    ]:
        with pytest.raises((exc_type, ValueError)) as caught:
            await call()
        messages += [str(caught.value), getattr(caught.value, "blocked_reason", "")]
    surfaces = {
        "object keys": "\n".join(harness.payloads.objects),
        "logs": "\n".join(r.getMessage() for r in caplog.records),
        "errors": "\n".join(messages),
        "result": json.dumps(
            {k: v for k, v in result.__dict__.items() if k != "records"}, default=str
        ),
        "ledger": json.dumps(ledger_rows(run), default=str),
    }
    assert caplog.records, "expected structured connector logs"
    for name, text in surfaces.items():
        assert not _leaks(text), (name, _leaks(text))


def test_vendor_names_live_only_in_allowed_places() -> None:
    from urllib.parse import urlsplit

    cfg = load_providers_config()
    # "Parallel" is an English word; its domain is searched instead. Domains come from config.
    names = [n.lower() for n in vendor_names(cfg).values() if n != "Parallel"]
    names += [
        ".".join((urlsplit(p.base_url).hostname or "").split(".")[-2:])
        for p in cfg.providers.values()
    ]
    offenders = []
    for path in list((REPO / "src").rglob("*.py")) + list((REPO / "tests").rglob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        if any(n in text for n in names):
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


def test_structured_logs_drop_fields_outside_the_allow_list(caplog) -> None:  # type: ignore[no-untyped-def]
    from account_intelligence.connectors.obs import log_event

    caplog.set_level(logging.INFO, logger="account_intelligence.connectors")
    log_event(
        "x",
        provider_code="p_search",
        page_text="SECRET PAGE TEXT",
        api_key="k",
        url="https://a?key=1",
    )
    line = json.loads(caplog.records[-1].getMessage())
    assert line == {"event": "x", "provider_code": "p_search"}
