"""Typed arguments, mandatory provenance, SSRF table, secret handling."""

from __future__ import annotations

import pickle
import typing
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from account_intelligence.connectors.base import Operation, ProviderCode
from account_intelligence.connectors.errors import SecurityError
from account_intelligence.connectors.models import (
    ARGS_FOR,
    FeedArgs,
    NewsEventRecord,
    Provenance,
    ReadPageArgs,
    Record,
    ScrapeJobPageArgs,
    WebPageRecord,
    WebSearchArgs,
)
from account_intelligence.connectors.security import (
    Secret,
    UrlPolicy,
    check_final_url,
    load_secret,
    redact,
    safe_headers,
    validate_target_url,
)
from tests.connectors.conftest import fake_resolver

PROV = dict(
    provider_code=ProviderCode.P_SEARCH,
    operation=Operation.WEB_SEARCH,
    source_url="https://x.example.com/a",
    retrieved_at=datetime(2026, 10, 2, tzinfo=UTC),
    raw_ref="tenants/t/accounts/a/runs/r/raw/abc.json.gz",
    page=1,
    index=0,
)


# ------------------------------------------------------------------ arguments


def test_no_argument_model_has_a_tenant_field_and_all_forbid_extras() -> None:
    for model in set(ARGS_FOR.values()):
        assert model.model_config.get("extra") == "forbid", model
        names = set(model.model_fields)
        assert not {n for n in names if "tenant" in n}, model


def test_extra_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        FeedArgs(domain="acme.com", tenant_id="x")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "domain",
    [
        "ACME.com",
        "www.acme.com",
        "acme",
        "acme..com",
        "-acme.com",
        "acme.com/",
        "https://acme.com",
        "",
    ],
)
def test_domain_pattern_matches_the_accounts_check(domain: str) -> None:
    with pytest.raises(ValidationError):
        FeedArgs(domain=domain)


def test_scrape_non_plain_mode_needs_a_cost_reason() -> None:
    with pytest.raises(ValidationError):
        ScrapeJobPageArgs(
            url="https://jobs.acme.com/1", page_kind="job_page", mode="json_extraction"
        )
    ok = ScrapeJobPageArgs(
        url="https://jobs.acme.com/1",
        page_kind="job_page",
        mode="json_extraction",
        expensive_mode_reason="structured salary table needed for row 14",
    )
    assert ok.mode == "json_extraction"


def test_scrape_only_on_job_pages() -> None:
    with pytest.raises(ValidationError):
        ScrapeJobPageArgs(url="https://acme.com/", page_kind="home_page")  # type: ignore[arg-type]


def test_search_queries_refuse_control_characters() -> None:
    with pytest.raises(ValidationError):
        WebSearchArgs(queries=("acme\x00 funding",))


# ------------------------------------------------------------------ provenance and records


def test_record_cannot_be_built_without_provenance_or_lineage() -> None:
    fields = dict(
        kind="web_page",
        url="https://x.example.com/a",
        title_text=None,
        publish_date=None,
        excerpts_text=(),
        full_content_text=None,
    )
    with pytest.raises(ValidationError):
        WebPageRecord(**fields, lineage_hint=None)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        WebPageRecord(**fields, provenance=Provenance(**PROV))  # type: ignore[arg-type]
    WebPageRecord(**fields, provenance=Provenance(**PROV), lineage_hint=None)  # type: ignore[arg-type]


@pytest.mark.parametrize("missing", list(PROV))
def test_provenance_fields_are_all_mandatory(missing: str) -> None:
    with pytest.raises(ValidationError):
        Provenance(**{k: v for k, v in PROV.items() if k != missing})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "when", [datetime(2026, 10, 2), datetime(2026, 10, 2, tzinfo=timezone(timedelta(hours=5)))]
)
def test_retrieved_at_must_be_aware_utc(when: datetime) -> None:
    with pytest.raises(ValidationError):
        Provenance(**{**PROV, "retrieved_at": when})


def test_record_fields_have_no_invented_defaults() -> None:
    """Missing is None and must be said explicitly: no record field has a default."""
    for cls in Record.__subclasses__():
        for name, info in cls.model_fields.items():
            assert info.is_required(), f"{cls.__name__}.{name} has a default"


@given(st.sampled_from(sorted(PROV)))
def test_property_no_record_without_provenance(missing: str) -> None:
    with pytest.raises(ValidationError):
        NewsEventRecord(
            kind="news_event",
            provenance={k: v for k, v in PROV.items() if k != missing},  # type: ignore[arg-type]
            lineage_hint=None,
            event_id="e",
            category="c",
            summary_text="s",
            found_at="2026",
            confidence=0.5,
            article_sentence_text=None,
            effective_date=None,
            amount_normalized_usd=None,
            planning=False,
            article_url=None,
            article_title_text=None,
            article_published_at=None,
        )


# ------------------------------------------------------------------ SSRF table


SSRF_SHAPE = [
    "http://acme.com/careers",  # non-https
    "https://user:pw@acme.com/",  # userinfo
    "https://acme.com@evil.com/",  # userinfo trick
    "https://127.0.0.1/",  # loopback
    "https://10.0.0.5/",  # private
    "https://192.168.1.1/",
    "https://172.16.0.1/",
    "https://169.254.169.254/latest/meta-data/",  # metadata address
    "https://[::1]/",
    "https://[fd00:ec2::254]/",
    "https://metadata.google.internal/",
    "https://localhost/",
    "https://acme.com:8443/",  # non-default port
    "ftp://acme.com/",
    "https://acme.com/a b",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", SSRF_SHAPE)
async def test_ssrf_shape_table_is_refused(url: str) -> None:
    with pytest.raises((SecurityError, ValidationError)):
        await validate_target_url(url, UrlPolicy(resolver=fake_resolver()))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "addresses",
    [
        ["10.1.2.3"],
        ["127.0.0.1"],
        ["169.254.169.254"],
        ["93.184.215.14", "192.168.0.10"],
        ["::1"],
        [],
    ],
)
async def test_dns_resolving_to_non_public_is_refused(addresses: list[str]) -> None:
    policy = UrlPolicy(resolver=fake_resolver({"jobs.acme.com": addresses}))
    with pytest.raises(SecurityError):
        await validate_target_url("https://jobs.acme.com/1", policy, account_domain="acme.com")


@pytest.mark.asyncio
async def test_page_host_must_be_on_the_account_allow_list() -> None:
    policy = UrlPolicy(resolver=fake_resolver())
    await validate_target_url("https://jobs.acme.com/1", policy, account_domain="acme.com")
    await validate_target_url(
        "https://boards.greenhouse.io/acme",
        policy,
        account_domain="acme.com",
        extra_allowed=["boards.greenhouse.io"],
    )
    with pytest.raises(SecurityError, match="allow-list"):
        await validate_target_url("https://other.com/x", policy, account_domain="acme.com")
    with pytest.raises(SecurityError, match="allow-list"):
        await validate_target_url("https://notacme.com/x", policy, account_domain="acme.com")


def test_redirect_to_private_or_off_list_is_refused() -> None:
    check_final_url("https://jobs.acme.com/1", "https://jobs.acme.com/1?x=1", "acme.com", ())
    with pytest.raises(SecurityError):
        check_final_url("https://jobs.acme.com/1", "https://10.0.0.1/admin", "acme.com", ())
    with pytest.raises(SecurityError):
        check_final_url("https://jobs.acme.com/1", "https://evil.com/", "acme.com", ())


def test_the_accounts_own_redirect_to_its_job_board_is_allowed() -> None:
    check_final_url(
        "https://www.acme.com/careers/finance",
        "https://acmeclinical.wd1.myworkdayjobs.com/Search",
        "acme.com",
        (),
    )
    check_final_url("https://acme.com/jobs", "https://careers-acme.icims.com/", "acme.com", ())
    # a job board reached from a host that is only on the extra list is not the account's word
    with pytest.raises(SecurityError):
        check_final_url(
            "https://partner.example/jobs",
            "https://x.myworkdayjobs.com/",
            "acme.com",
            ["partner.example"],
        )
    # the top-level domain never counts and an ordinary host is still refused
    with pytest.raises(SecurityError):
        check_final_url("https://acme.com/careers", "https://evil.jobs/", "acme.com", ())
    with pytest.raises(SecurityError):
        check_final_url("https://acme.com/careers", "https://evil.com/careers", "acme.com", ())


def test_read_page_args_refuse_bad_urls_at_construction() -> None:
    with pytest.raises((ValidationError, SecurityError)):
        ReadPageArgs(url="http://acme.com/")


# ------------------------------------------------------------------ secrets


def test_secret_never_prints_or_pickles() -> None:
    s = Secret("sk-live-abcdef123456")
    assert "abcdef" not in repr(s) and "abcdef" not in str(s) and "abcdef" not in f"{s}"
    with pytest.raises(TypeError):
        pickle.dumps(s)


def test_redaction_of_errors_and_headers(tmp_path: typing.Any) -> None:
    s = Secret("my-very-secret-value-1")
    assert "my-very-secret" not in redact("failed with key my-very-secret-value-1", [s])
    assert "Bearer" not in redact("Authorization: Bearer abcdefghijklmnop1234")
    assert safe_headers([("Authorization", "Bearer x"), ("x-api-key", "k"), ("accept", "a")]) == {
        "authorization": "[REDACTED]",
        "x-api-key": "[REDACTED]",
        "accept": "a",
    }
    f = tmp_path / "key"
    f.write_text("from-file-secret\n")
    loaded = load_secret({"X_FILE": str(f), "X": "env-value"}, "X")
    assert loaded is not None and loaded.reveal() == "from-file-secret"
