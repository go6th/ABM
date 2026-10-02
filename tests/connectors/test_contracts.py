"""Contract tests: vendor-example cassettes -> normalised records, byte for byte.

Cassettes are built only from first-party examples (cassettes/build_cassettes.py).
Golden files hold the expected normalised records; a change in either the cassette
shape or the normaliser fails here. Regenerate goldens only on purpose:
``REGEN_GOLDEN=1 pytest tests/connectors/test_contracts.py`` and review the diff.
"""

from __future__ import annotations

import copy
import json
import os
import re
from datetime import UTC, datetime
from typing import Any

import pytest

from account_intelligence.connectors.base import Operation, ProviderCode, RawResponse
from account_intelligence.connectors.errors import (
    NotSupportedYet,
    PolicyRefused,
    RetryableProviderError,
    SchemaDriftError,
)
from account_intelligence.connectors.factory import build_connectors
from account_intelligence.connectors.models import (
    FeedArgs,
    OperationArgs,
    OrgResolveArgs,
    OrgTechStackArgs,
    PeopleByTechnologyArgs,
    PeopleByTitleArgs,
    ReadPageArgs,
    ScrapeJobPageArgs,
    WebSearchArgs,
)
from account_intelligence.connectors.settings import load_providers_config
from tests.connectors.conftest import CASSETTES, GOLDEN, TEST_ENV

CONNECTORS = build_connectors(load_providers_config())


def _example(code: str, name: str) -> Any:
    return json.loads((CASSETTES / code / f"{name}.json").read_text())["response"]["body"]


# Inputs echoed by the vendor examples (kept out of test source: names live in docs/providers/).
SCRAPE_URL = (
    _example("p_scrape", "scrape_job_page")["data"]["metadata"]["sourceURL"].rstrip("/") + "/"
)
ORG_DOMAIN = _example("p_org", "org_resolve")["organizations"][0]["input"]["url"]
BASE_TIME = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)

CASES: list[tuple[ProviderCode, Operation, str, OperationArgs]] = [
    (
        ProviderCode.P_SEARCH,
        Operation.WEB_SEARCH,
        "web_search",
        WebSearchArgs(queries=("example query",)),
    ),
    (
        ProviderCode.P_SEARCH,
        Operation.READ_PAGE,
        "read_page",
        ReadPageArgs(url="https://www.example.com/"),
    ),
    (
        ProviderCode.P_SCRAPE,
        Operation.SCRAPE_JOB_PAGE,
        "scrape_job_page",
        ScrapeJobPageArgs(url=SCRAPE_URL, page_kind="job_page"),
    ),
    (ProviderCode.P_SIGNALS, Operation.NEWS_EVENTS, "news_events", FeedArgs(domain="example.com")),
    (
        ProviderCode.P_SIGNALS,
        Operation.JOB_OPENINGS,
        "job_openings",
        FeedArgs(domain="example.com"),
    ),
    (
        ProviderCode.P_SIGNALS,
        Operation.TECH_DETECTIONS,
        "tech_detections",
        FeedArgs(domain="example.com"),
    ),
    (ProviderCode.P_ORG, Operation.ORG_RESOLVE, "org_resolve", OrgResolveArgs(domain=ORG_DOMAIN)),
    (
        ProviderCode.P_ORG,
        Operation.ORG_TECH_STACK,
        "org_tech_stack",
        OrgTechStackArgs(organization_id=1726684),
    ),
    (
        ProviderCode.P_ORG,
        Operation.PEOPLE_BY_TECHNOLOGY,
        "people_poll_succeeded",
        PeopleByTechnologyArgs(organization_id=1726684, technology_slug="kubernetes"),
    ),
]


def _cassette(code: ProviderCode, name: str) -> dict[str, Any]:
    return json.loads((CASSETTES / code / f"{name}.json").read_text())


def _raw(body: Any, status: int = 200) -> RawResponse:
    return RawResponse(status, "application/json", json.dumps(body).encode(), {}, 12, "/x")


def _base(code: ProviderCode, op: Operation) -> dict[str, Any]:
    return {
        "provider_code": code,
        "operation": op,
        "raw_ref": f"golden/{code}/{op}",
        "retrieved_at": BASE_TIME,
    }


def _normalise(code: ProviderCode, op: Operation, args: OperationArgs, body: Any) -> str:
    parsed = CONNECTORS[code].parse(op, args, _raw(body), _base(code, op), None)
    payload = {
        "items_read": parsed.items_read,
        "next_cursor": parsed.next_cursor,
        "provider_credits": parsed.provider_credits,
        "warnings": list(parsed.warnings),
        "records": [r.model_dump(mode="json") for r in parsed.records],
    }
    return json.dumps(payload, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


@pytest.mark.parametrize(("code", "op", "name", "args"), CASES, ids=[c[2] for c in CASES])
def test_golden_normalisation_byte_for_byte(
    code: ProviderCode, op: Operation, name: str, args: OperationArgs
) -> None:
    out = _normalise(code, op, args, _cassette(code, name)["response"]["body"])
    golden = GOLDEN / code / f"{name}.json"
    if os.getenv("REGEN_GOLDEN") == "1":
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(out)
    assert golden.exists(), f"missing golden {golden}; regenerate deliberately"
    assert out == golden.read_text()


@pytest.mark.parametrize(("code", "op", "name", "args"), CASES, ids=[c[2] for c in CASES])
def test_normalisation_is_reproducible(
    code: ProviderCode, op: Operation, name: str, args: OperationArgs
) -> None:
    body = _cassette(code, name)["response"]["body"]
    assert _normalise(code, op, args, body) == _normalise(code, op, args, copy.deepcopy(body))


def _drop(body: Any, path: list[str | int]) -> Any:
    out = copy.deepcopy(body)
    cur = out
    for step in path[:-1]:
        cur = cur[step]
    del cur[path[-1]]
    return out


def _rename(body: Any, path: list[str | int], new: str) -> Any:
    out = copy.deepcopy(body)
    cur = out
    for step in path[:-1]:
        cur = cur[step]
    cur[new] = cur.pop(path[-1])
    return out


DRIFT = [
    (0, ["results", 0, "url"]),
    (0, ["results"]),
    (1, ["results", 0, "excerpts"]),
    (1, ["errors"]),
    (2, ["data", "metadata"]),
    (2, ["success"]),
    (3, ["data", 0, "attributes", "found_at"]),
    (3, ["included"]),
    (4, ["data", 0, "attributes", "url"]),
    (4, ["data", 0, "attributes", "first_seen_at"]),
    (5, ["data", 0, "attributes", "source_count"]),
    (5, ["data", 0, "relationships", "technology"]),
    (6, ["credits_used"]),
    (7, ["business_functions", 0, "categories", 0, "technologies", 0, "slug"]),
    (8, ["total"]),
]


@pytest.mark.parametrize(
    ("case", "path"), DRIFT, ids=[f"{CASES[c][2]}-{'.'.join(map(str, p))}" for c, p in DRIFT]
)
def test_schema_drift_is_refused_not_repaired(case: int, path: list[str | int]) -> None:
    code, op, name, args = CASES[case]
    body = _cassette(code, name)["response"]["body"]
    with pytest.raises(SchemaDriftError):
        CONNECTORS[code].parse(op, args, _raw(_drop(body, path)), _base(code, op), None)
    renamed = _rename(body, path, f"{path[-1]}_renamed") if isinstance(path[-1], str) else None
    if renamed is not None:
        with pytest.raises(SchemaDriftError):
            CONNECTORS[code].parse(op, args, _raw(renamed), _base(code, op), None)


def test_wrong_types_are_drift_not_coerced() -> None:
    code, op, name, args = CASES[3]
    body = copy.deepcopy(_cassette(code, name)["response"]["body"])
    body["data"][0]["attributes"]["confidence"] = "0.9"  # string where a number is documented
    with pytest.raises(SchemaDriftError):
        CONNECTORS[code].parse(op, args, _raw(body), _base(code, op), None)


def test_malformed_json_is_drift() -> None:
    raw = RawResponse(200, "application/json", b"{not json", {}, 1, "/x")
    with pytest.raises(SchemaDriftError):
        CONNECTORS[ProviderCode.P_SEARCH].parse(
            Operation.WEB_SEARCH,
            CASES[0][3],
            raw,
            _base(ProviderCode.P_SEARCH, Operation.WEB_SEARCH),
            None,
        )


def test_unknown_extra_fields_are_ignored_in_records() -> None:
    code, op, name, args = CASES[0]
    body = copy.deepcopy(_cassette(code, name)["response"]["body"])
    body["brand_new_field"] = {"x": 1}
    body["results"][0]["also_new"] = "y"
    assert _normalise(code, op, args, body) == _normalise(
        code, op, args, _cassette(code, name)["response"]["body"]
    )


# ------------------------------------------------------------------ provider specifics


def test_signals_pagination_uses_the_documented_count() -> None:
    code, op, name, args = CASES[3]
    body = copy.deepcopy(_cassette(code, name)["response"]["body"])
    body["meta"]["count"] = 250
    conn = CONNECTORS[code]
    assert conn.parse(op, args, _raw(body), _base(code, op), None).next_cursor == "2"
    assert conn.parse(op, args, _raw(body), _base(code, op), "3").next_cursor is None


def test_signals_requests_carry_page_limit_and_delta_filter() -> None:
    from datetime import date

    conn = CONNECTORS[ProviderCode.P_SIGNALS]
    req = conn.build_request(
        Operation.JOB_OPENINGS, FeedArgs(domain="acme.com", since=date(2026, 9, 1)), "2"
    )
    assert req.path == "/companies/acme.com/job_openings"
    assert req.params == {"page": 2, "limit": 100, "first_seen_at_from": "2026-09-01"}


def test_signals_403_is_a_rate_limit_for_this_provider() -> None:
    from account_intelligence.connectors.errors import RateLimited

    err = CONNECTORS[ProviderCode.P_SIGNALS].classify_error(Operation.NEWS_EVENTS, _raw({}, 403))
    assert isinstance(err, RateLimited)


def test_org_people_start_poll_and_paging() -> None:
    conn = CONNECTORS[ProviderCode.P_ORG]
    args = CASES[8][3]
    start = conn.parse(
        Operation.PEOPLE_BY_TECHNOLOGY,
        args,
        _raw(_cassette(ProviderCode.P_ORG, "people_start")["response"]["body"]),
        _base(ProviderCode.P_ORG, Operation.PEOPLE_BY_TECHNOLOGY),
        None,
    )
    assert start.pending_job_id == "019d..." and start.records == ()
    req = conn.build_request(Operation.PEOPLE_BY_TECHNOLOGY, args, None)
    assert req.json_body is not None and "file_name" not in req.json_body
    assert req.json_body["filter"]["query"] == {"query": "technology EQ 'kubernetes'"}
    assert conn.poll_request(Operation.PEOPLE_BY_TECHNOLOGY, "j1").json_body == {"request_id": "j1"}
    failed = {"credits_used": 0, "credits_remaining": 1, "request_id": "j1", "status": "failed"}
    with pytest.raises(RetryableProviderError):
        conn.parse(
            Operation.PEOPLE_BY_TECHNOLOGY,
            args,
            _raw(failed),
            _base(ProviderCode.P_ORG, Operation.PEOPLE_BY_TECHNOLOGY),
            None,
        )


def test_org_hosted_file_response_is_refused() -> None:
    body = copy.deepcopy(_cassette(ProviderCode.P_ORG, "org_tech_stack")["response"]["body"])
    body["file"] = {"id": "f", "download_url": "https://x"}
    with pytest.raises(PolicyRefused):
        CONNECTORS[ProviderCode.P_ORG].parse(
            Operation.ORG_TECH_STACK,
            CASES[7][3],
            _raw(body),
            _base(ProviderCode.P_ORG, Operation.ORG_TECH_STACK),
            None,
        )


def test_unsourced_operations_raise_not_supported_with_what_to_supply() -> None:
    people = CONNECTORS[ProviderCode.P_PEOPLE]
    assert not people.supports(Operation.PEOPLE_BY_TITLE)
    with pytest.raises(NotSupportedYet, match="grid"):
        people.build_request(
            Operation.PEOPLE_BY_TITLE, PeopleByTitleArgs(domain="acme.com", titles=("CTO",)), None
        )
    assert not CONNECTORS[ProviderCode.P_ORG].supports(Operation.ORG_TEAMS)
    scrape = ScrapeJobPageArgs(
        url="https://jobs.acme.com/1",
        page_kind="job_page",
        mode="json_extraction",
        expensive_mode_reason="needed for a structured salary table",
    )
    with pytest.raises(NotSupportedYet):
        CONNECTORS[ProviderCode.P_SCRAPE].build_request(Operation.SCRAPE_JOB_PAGE, scrape, None)


# ------------------------------------------------------------------ cassette hygiene

KEY_PATTERNS = [
    re.compile(
        r"(?i)\b(authorization|x-api-key|x-api-token|api[_-]?key|api[_-]?token)\b\W{0,4}[:=]\W{0,4}[A-Za-z0-9._\-]{8,}"
    ),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"\bsk-(live|test)-[A-Za-z0-9]{6,}"),
    re.compile(r"\bfc-[a-f0-9]{24,}\b"),
]


def test_no_cassette_or_golden_contains_a_key_like_string() -> None:
    files = list(CASSETTES.rglob("*.json")) + list(GOLDEN.rglob("*.json"))
    assert files
    for f in files:
        text = f.read_text()
        for value in TEST_ENV.values():
            assert value not in text, f
        for pattern in KEY_PATTERNS:
            assert not pattern.search(text), (f, pattern.pattern)


def test_key_scanner_catches_planted_keys() -> None:
    planted = (
        '{"headers": {"Authorization": "Bearer abcdefghijklmnopqrstuvwx"},'
        ' "x-api-key": "k3y-abcdef12"}'
    )
    assert any(p.search(planted) for p in KEY_PATTERNS)
