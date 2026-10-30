"""Kill tests for the mutation run (2026-10-30) of ``agents.production.jobs_carry``: the carried
claim is matched to the previous run's graded fact by attribute, value and normalised address, and
that fact is recorded again for the new run through the grading engine's one write path. The
transaction and the write path are recording fakes; the two statements are pinned. Hermetic."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from account_intelligence.grading.ports import RunInfo
from agents.production import jobs_carry as JC

TENANT = UUID("00000000-0000-0000-0000-0000000000a1")
ACCOUNT = UUID("00000000-0000-0000-0000-0000000000b2")
PREVIOUS = UUID("00000000-0000-0000-0000-0000000000c3")
RUN = RunInfo(
    tenant_id=TENANT,
    account_id=ACCOUNT,
    run_id=UUID("00000000-0000-0000-0000-0000000000d4"),
    started_at=datetime(2026, 10, 30, tzinfo=UTC),
    account_domain="acme.example",
)
FACTS_SQL = (
    "SELECT * FROM facts WHERE run_id = :p AND account_id = :a"
    " AND superseded_at IS NULL AND attribute_key LIKE 'jobs.%'"
    " AND config_hash = :h ORDER BY id"
)
EVIDENCE_SQL = "SELECT * FROM fact_evidence WHERE fact_id = ANY(:ids) ORDER BY id"
WHEN = datetime(2026, 9, 1, tzinfo=UTC)


def _fact(i: int, key: str, value: str, **kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": i,
        "attribute_key": key,
        "value_text": value,
        "value_json": {"normalised": "norm"},
        "schema_version": 1,
        "config_hash": "cfg",
        "claim_hash": f"h{i}",
        "claim_kind": "sourced",
        "engine_grade": "A",
        "product_grade": "confirmed",
        "confidence": 0.75,
        "read_in_primary_this_run": True,
        "may_state_to_prospect": True,
        "precedence_rule": "rule",
        "confirming_question": None,
        "reasoning": "why",
        "flags": ["z", "a"],
        "lead_status": None,
        "lane": "salesforce",
    }
    return SimpleNamespace(**{**base, **kw})


def _evidence(fact_id: int, url: str | None, **kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "fact_id": fact_id,
        "evidence_kind": "source",
        "url": url,
        "supporting_sentence": "s",
        "span_sha256": "ab",
        "source_ref": "S-0001",
        "call_ref": "c1",
        "origin": "full_page_read",
        "source_tier": "T1",
        "lineage_group": "g",
        "is_aggregator": 0,
        "date_kind": "published",
        "evidence_date": WHEN,
        "provider_code": "S-0001",
    }
    return SimpleNamespace(**{**base, **kw})


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def all(self) -> list[Any]:
        return self.rows


class _Session:
    def __init__(self, facts: list[Any], evidence: list[Any]) -> None:
        self.facts, self.evidence = facts, evidence
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> _Result:
        sql = str(statement)
        self.calls.append((sql, params))
        return _Result(self.facts if "FROM facts" in sql else self.evidence)


def _run_carry(facts, evidence, claims, monkeypatch, config_hash="cfg"):  # type: ignore[no-untyped-def]
    session = _Session(facts, evidence)
    opened: list[UUID] = []
    recorded: list[dict[str, Any]] = []
    pinned: list[str] = []

    @asynccontextmanager
    async def txn(tenant: UUID):  # type: ignore[no-untyped-def]
        opened.append(tenant)
        yield session

    async def pin(t: Any, run: RunInfo, config: str) -> str:
        assert t is txn and run is RUN
        pinned.append(config)
        return config

    async def record(t: Any, run: RunInfo, claim: Any, *, lane=None, event_date=None):  # type: ignore[no-untyped-def]
        assert t is txn and run is RUN
        recorded.append({"claim": claim, "lane": lane, "event_date": event_date})
        return UUID(int=len(recorded))

    monkeypatch.setattr(JC.repo, "pin_config", pin)
    monkeypatch.setattr(JC.repo, "record_graded_fact", record)
    result = asyncio.run(JC.carry_job_facts(txn, RUN, PREVIOUS, claims, config_hash))
    return result, session, opened, recorded, pinned


def _claim(local_id: str, key: str, value: str, *urls: str) -> dict[str, Any]:
    return {
        "local_id": local_id,
        "attribute_key": key,
        "value": value,
        "evidence": [{"url": u} for u in urls],
    }


# ---- the address ------------------------------------------------------------------------------


def test_an_address_is_compared_in_its_normal_form_else_trimmed() -> None:
    assert (
        JC._address("HTTPS://Jobs.Example/p/1/?utm_source=x&b=2#frag")
        == "https://jobs.example/p/1?b=2"
    )
    assert JC._address("  not a url  ") == "not a url"
    assert JC._address("ftp://jobs.example/p") == "ftp://jobs.example/p"


# ---- the graded claim a stored fact and its evidence record -----------------------------------


def test_a_stored_fact_and_its_evidence_make_the_same_graded_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fact = _fact(
        1, "jobs.salesforce.postings", "12 postings", confirming_question="Is it still open?"
    )
    ev = [
        _evidence(1, "https://jobs.example/p/1"),
        _evidence(
            1, "https://jobs.example/p/2", is_aggregator=1, evidence_date=WHEN, date_kind="observed"
        ),
    ]
    (carried, unmatched), session, opened, recorded, pinned = _run_carry(
        [fact],
        ev,
        [_claim("c1", "jobs.salesforce.postings", "12 postings", "https://jobs.example/p/1")],
        monkeypatch,
    )
    assert (carried, unmatched) == ({"c1"}, set())
    graded = recorded[0]["claim"]
    assert (graded.schema_version, graded.config_hash, graded.claim_hash) == (1, "cfg", "h1")
    assert (graded.claim_kind, graded.attribute_key, graded.value) == (
        "sourced",
        "jobs.salesforce.postings",
        "12 postings",
    )
    assert (graded.normalised_value, graded.product_grade, graded.engine_grade) == (
        "norm",
        "confirmed",
        "A",
    )
    assert graded.confidence == 0.75 and isinstance(graded.confidence, float)
    assert graded.read_in_primary_this_run is True and graded.may_state_to_prospect is True
    assert (graded.precedence_rule, graded.confirming_question, graded.reasoning) == (
        "rule",
        "Is it still open?",
        "why",
    )
    assert graded.flags == ("a", "z")  # the claim keeps its flags sorted
    assert graded.lead is None
    first, second = graded.evidence
    assert (first.evidence_kind, first.url, first.supporting_sentence, first.span_sha256) == (
        "source",
        "https://jobs.example/p/1",
        "s",
        "ab",
    )
    assert (first.source_ref, first.call_ref, first.origin, first.tier) == (
        "S-0001",
        "c1",
        "full_page_read",
        "T1",
    )
    assert (first.lineage_group, first.is_aggregator, first.date_kind) == ("g", False, "published")
    assert (first.evidence_date, first.provider_code) == (WHEN, "S-0001")
    assert second.is_aggregator is True and second.date_kind == "observed"
    assert recorded[0]["lane"] == "salesforce" and recorded[0]["event_date"] is None
    assert pinned == ["cfg"] and opened == [
        TENANT,
    ]


def test_a_fact_without_json_value_or_confidence_or_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    fact = _fact(1, "jobs.a.b", "v", value_json=None, confidence=None, flags=None, lane=None)
    (carried, _), *_rest, recorded, _ = _run_carry(
        [fact],
        [_evidence(1, "https://jobs.example/p")],
        [_claim("c", "jobs.a.b", "v", "https://jobs.example/p")],
        monkeypatch,
    )
    graded = recorded[0]["claim"]
    assert carried == {"c"}
    assert (graded.normalised_value, graded.confidence, graded.flags) == ("v", None, ())
    assert recorded[0]["lane"] is None


def test_a_lead_keeps_its_status_and_a_missing_status_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for status, expected in (("complete", "complete"), (None, "pending")):
        fact = _fact(
            1,
            "jobs.a.b",
            "v",
            product_grade="lead",
            engine_grade="C",
            read_in_primary_this_run=False,
            may_state_to_prospect=False,
            lead_status=status,
        )
        *_, recorded, _ = _run_carry(
            [fact],
            [_evidence(1, "https://jobs.example/p")],
            [_claim("c", "jobs.a.b", "v", "https://jobs.example/p")],
            monkeypatch,
        )
        lead = recorded[0]["claim"].lead
        assert lead is not None and lead.status == expected
        assert (lead.reason, lead.accepted_queries, lead.tool_categories) == (None, 0, ())


def test_the_event_date_is_recorded_as_text(monkeypatch: pytest.MonkeyPatch) -> None:
    dated = _fact(1, "jobs.a.b", "v", value_json={"normalised": "n", "event_date": "2026-08-30"})
    *_, recorded, _ = _run_carry(
        [dated],
        [_evidence(1, "https://jobs.example/p")],
        [_claim("c", "jobs.a.b", "v", "https://jobs.example/p")],
        monkeypatch,
    )
    assert recorded[0]["event_date"] == "2026-08-30"
    blank = _fact(1, "jobs.a.b", "v", value_json={"event_date": ""})
    *_, recorded, _ = _run_carry(
        [blank],
        [_evidence(1, "https://jobs.example/p")],
        [_claim("c", "jobs.a.b", "v", "https://jobs.example/p")],
        monkeypatch,
    )
    assert recorded[0]["event_date"] is None


# ---- the matching -----------------------------------------------------------------------------


def test_the_two_statements_and_their_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = [_fact(7, "jobs.a.b", "v"), _fact(9, "jobs.a.c", "w")]
    _, session, _, _, pinned = _run_carry(facts, [], [], monkeypatch, config_hash="cfg-9")
    assert pinned == ["cfg-9"]
    assert session.calls == [
        (FACTS_SQL, {"p": PREVIOUS, "a": ACCOUNT, "h": "cfg-9"}),
        (EVIDENCE_SQL, {"ids": [7, 9]}),
    ]


def test_a_claim_matches_by_attribute_value_and_normalised_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = [_fact(1, "jobs.a.b", "v1"), _fact(2, "jobs.a.b", "v2"), _fact(3, "jobs.a.c", "v1")]
    evidence = [
        _evidence(1, "https://Jobs.example/p/1/?utm_source=x"),
        _evidence(2, "https://jobs.example/p/1"),
        _evidence(3, "https://jobs.example/p/1"),
    ]
    claims = [
        _claim("one", "jobs.a.b", "v1", "https://jobs.example/p/1"),
        _claim("two", "jobs.a.b", "v2", "https://jobs.example/other", "https://jobs.example/p/1"),
        _claim("three", "jobs.a.c", "v2", "https://jobs.example/p/1"),  # no such value
        _claim("four", "jobs.a.b", "v1", "https://jobs.example/nowhere"),
        _claim("five", "jobs.a.b", "v1"),  # no evidence at all
    ]
    (carried, unmatched), _, _, recorded, _ = _run_carry(facts, evidence, claims, monkeypatch)
    assert carried == {"one", "two"}
    assert unmatched == {"three", "four", "five"}
    assert [r["claim"].claim_hash for r in recorded] == ["h1", "h2"]  # in the claims' order


def test_a_fact_with_several_addresses_is_found_by_any_and_the_first_fact_of_a_key_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facts = [_fact(1, "jobs.a.b", "v"), _fact(2, "jobs.a.b", "v")]
    evidence = [
        _evidence(1, "https://jobs.example/p/1"),
        _evidence(1, "https://jobs.example/p/2"),
        _evidence(2, "https://jobs.example/p/1"),
        _evidence(2, None),
    ]
    claims = [
        _claim("a", "jobs.a.b", "v", "https://jobs.example/p/2"),
        _claim("b", "jobs.a.b", "v", "https://jobs.example/p/1"),
    ]
    (carried, _), _, _, recorded, _ = _run_carry(facts, evidence, claims, monkeypatch)
    assert carried == {"a", "b"}
    assert [r["claim"].claim_hash for r in recorded] == ["h1", "h1"]  # both resolve to fact 1


def test_a_claim_without_a_local_id_is_listed_under_the_empty_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = {
        "attribute_key": "jobs.a.b",
        "value": "v",
        "evidence": [{"url": "https://jobs.example/x"}],
    }
    (carried, unmatched), *_ = _run_carry([], [], [claim], monkeypatch)
    assert (carried, unmatched) == (set(), {""})


def test_a_fact_of_the_previous_run_with_no_address_is_never_a_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (carried, unmatched), *_ = _run_carry(
        [_fact(1, "jobs.a.b", "v")],
        [_evidence(1, None), _evidence(1, "")],
        [_claim("c", "jobs.a.b", "v", "None", "")],
        monkeypatch,
    )
    assert (carried, unmatched) == (set(), {"c"})
