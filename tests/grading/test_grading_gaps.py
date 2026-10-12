"""Grading rules that the main suites never reached: drop codes, default reasoning, invisible
values, registry domains beyond the first, and the fact repository's refusals and reuse.

Drop codes are stored in ``claim_drops`` and counted on dashboards, so their mapping is pinned
here. A value made only of invisible characters must be dropped, never stored as an empty fact.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from pydantic import ValidationError

from account_intelligence.grading import repo
from account_intelligence.grading.config import GradingConfig, RegistryEntry
from account_intelligence.grading.integration import judge_profile_ok
from account_intelligence.grading.lineage import profile_for
from account_intelligence.grading.pipeline import (
    GradingInputs,
    _drop_reason,
    _reasoning,
    grade_claims,
)
from account_intelligence.grading.schema import GradedClaim
from tests.grading.conftest import NOW
from tests.grading.db_world import evidence, graded_confirmed, run_info
from tests.grading.world import CAREERS, CAREERS_TEXT, RUN_INFO, sourced, world


def _validation_error(**over: object) -> ValidationError:
    try:
        graded_confirmed(**over)
    except ValidationError as error:
        return error
    raise AssertionError("expected the graded claim to be refused")


def test_validation_failures_map_to_stable_drop_codes() -> None:
    hypothesis = _validation_error(
        product_grade="hypothesis",
        engine_grade=None,
        read_in_primary_this_run=False,
        may_state_to_prospect=False,
        confirming_question=None,
    )
    probable = _validation_error(
        product_grade="probable", read_in_primary_this_run=False, may_state_to_prospect=False
    )
    other = _validation_error(value="")
    assert _drop_reason(hypothesis) == "missing_confirming_question"
    assert _drop_reason(probable) == "missing_reasoning"
    assert _drop_reason(other) == "schema_violation"


def test_reasoning_defaults_to_the_engine_and_never_overrides_given_text() -> None:
    assert _reasoning(None, False, None) == "Graded by the evidence engine."
    assert _reasoning(None, True, "Stated by the careers page.") == "Stated by the careers page."


@pytest.mark.asyncio
async def test_a_synthesis_made_of_invisible_characters_is_dropped(cfg: GradingConfig) -> None:
    w = world()
    basis = sourced(
        "b1",
        "jobs.salesforce_admin.open",
        "Senior Salesforce Administrator",
        (CAREERS, CAREERS_TEXT.split(". ")[0] + "."),
    )
    ghost = {
        "claim_kind": "synthesis",
        "local_id": "s1",
        "attribute_key": "jobs.crm.investment",
        "value": "​​​",
        "basis_claim_ids": ["b1"],
        "confirming_question": "Is the CRM team growing?",
        "reasoning": "Inferred from the open role.",
    }
    result = await grade_claims(
        [basis, ghost], RUN_INFO, GradingInputs(w.sources, w.fetches), cfg, NOW
    )
    assert [g.attribute_key for g in result.graded] == ["jobs.salesforce_admin.open"]
    assert [d.reason for d in result.dropped] == ["schema_violation"]


def test_registry_domains_after_the_first_and_unlisted_hosts(cfg: GradingConfig) -> None:
    entries = (
        RegistryEntry(
            domain="news.example.org",
            tier="T2",
            is_aggregator=False,
            lineage_group="example-news",
            live_state=False,
            source="test",
        ),
        RegistryEntry(
            domain="wire.example.com",
            tier="T3",
            is_aggregator=True,
            lineage_group="example-wire",
            live_state=False,
            source="test",
        ),
    )
    reg = cfg.registry.model_copy(update={"domains": entries})
    configured = cfg.model_copy(update={"registry": reg})
    source = world().sources[3]  # a search snippet
    on_wire = dataclasses.replace(source, host="feeds.wire.example.com")
    second = profile_for(on_wire, RUN_INFO, configured)
    assert (second.tier, second.is_aggregator, second.lineage_group, second.flags) == (
        "T3",
        True,
        "example-wire",
        ("aggregator",),
    )
    unlisted = profile_for(
        dataclasses.replace(source, host="elsewhere.example.net"), RUN_INFO, configured
    )
    assert unlisted.lineage_group == "host:elsewhere.example.net" and not unlisted.lineage_known


def test_judge_profile_is_ok_only_when_judge_and_generator_differ(cfg: GradingConfig) -> None:
    from account_intelligence.grading.judge import model_profile

    differs = model_profile(cfg.judge.judge_task) != model_profile(cfg.judge.generator_task)
    assert judge_profile_ok(cfg) is differs
    same = cfg.model_copy(
        update={"judge": cfg.judge.model_copy(update={"judge_task": cfg.judge.generator_task})}
    )
    assert judge_profile_ok(same) is False


# Fact repository ------------------------------------------------------------------------------


def _admin(sql: str, *args: object) -> list[tuple[object, ...]]:
    from tests.conftest import ADMIN_DSN

    with psycopg.connect(ADMIN_DSN, autocommit=True) as c:
        cur = c.execute(sql, args)
        return list(cur.fetchall()) if cur.description else []


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_a_fact_for_a_run_in_neither_lifecycle_is_refused(tenant_a: UUID) -> None:
    from account_intelligence.db.session import tenant_txn
    from tests.connectors.conftest import make_run

    account, _run, _ = make_run(tenant_a)
    ghost_run = uuid4()
    with pytest.raises(LookupError, match="missing from both run lifecycles"):
        await repo.record_graded_fact(
            tenant_txn, run_info(tenant_a, account, ghost_run), graded_confirmed()
        )
    assert _admin("SELECT count(*) FROM facts WHERE account_id = %s", account) == [(0,)]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_absence_proof_is_stored_without_inventing_a_source(tenant_a: UUID) -> None:
    from account_intelligence.db.session import tenant_txn
    from tests.connectors.conftest import make_run

    account, run, _ = make_run(tenant_a)
    absence = graded_confirmed(
        claim_hash="h-absence",
        claim_kind="absence",
        attribute_key="news.layoffs.recent",
        value="No layoffs were reported",
        normalised_value="No layoffs were reported",
        product_grade="hypothesis",
        engine_grade=None,
        confidence=None,
        read_in_primary_this_run=False,
        may_state_to_prospect=False,
        precedence_rule="no_evidence",
        evidence=(
            evidence(
                evidence_kind="search_proof",
                url=None,
                supporting_sentence="No supporting source found by web_search (web_search)",
                span_sha256=None,
                source_ref=None,
                tier=None,
                origin="search_snippet",
                lineage_group=None,
                date_kind=None,
                evidence_date=None,
            ),
        ),
        confirming_question="Has the company announced any layoffs?",
        flags=("absence",),
    )
    fact = await repo.record_graded_fact(tenant_txn, run_info(tenant_a, account, run), absence)
    rows = _admin(
        "SELECT evidence_kind, url, source_id FROM fact_evidence WHERE fact_id = %s", fact
    )
    assert rows == [("search_proof", None, None)]
    assert _admin("SELECT count(*) FROM sources WHERE account_id = %s", account) == [(0,)]


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_superseding_onto_an_existing_fact_reuses_it(tenant_a: UUID) -> None:
    """A downgrade whose new content already exists as a fact points the old fact at it and
    writes no duplicate evidence; the grade change is still recorded."""
    from account_intelligence.db.session import tenant_txn
    from tests.connectors.conftest import make_run

    account, run, _ = make_run(tenant_a)
    info = run_info(tenant_a, account, run)
    old = await repo.record_graded_fact(tenant_txn, info, graded_confirmed())
    target = graded_confirmed(
        claim_hash="h-other",
        value="Salesforce Administrator",
        normalised_value="Salesforce Administrator",
    )
    existing = await repo.record_graded_fact(tenant_txn, info, target)
    evidence_before = _admin("SELECT count(*) FROM fact_evidence WHERE fact_id = %s", existing)[0][
        0
    ]
    new = await repo.supersede(tenant_txn, info, old, target, reason_code="judge_cap")
    assert new == existing
    assert _admin("SELECT superseded_by FROM facts WHERE id = %s", old) == [(existing,)]
    assert (
        _admin("SELECT count(*) FROM fact_evidence WHERE fact_id = %s", existing)[0][0]
        == evidence_before
    )
    events = _admin("SELECT reason_code FROM grade_events WHERE fact_id = %s ORDER BY id", existing)
    assert [e for (e,) in events] == ["initial", "judge_cap"]


def test_the_docs_module_runs_as_a_script_in_check_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["docs", "--check"])
    with pytest.raises(SystemExit) as exited:
        runpy.run_module("account_intelligence.grading.docs", run_name="__main__")
    assert exited.value.code == 0  # the committed documents are current


def test_graded_claim_rules_refuse_a_hypothesis_without_its_question() -> None:
    with pytest.raises(ValidationError, match="confirming question"):
        GradedClaim(
            **{
                **graded_confirmed().model_dump(),
                "product_grade": "hypothesis",
                "engine_grade": None,
                "read_in_primary_this_run": False,
                "may_state_to_prospect": False,
                "confirming_question": None,
            }
        )
    assert NOW - timedelta(0) == NOW


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_an_independent_judge_confirms_and_snippet_leads_are_recorded(tenant_a: UUID) -> None:
    """With a judge that is not the generator's model, a primary read is Confirmed (not capped)
    and a claim resting on a search snippet alone is a Lead whose verification is stored."""
    from account_intelligence.db.session import tenant_txn
    from account_intelligence.grading.config import load_grading_config
    from account_intelligence.grading.integration import (
        DistillGradeDeps,
        make_distill_grade_handler,
    )
    from account_intelligence.workflow.spec import load_spec
    from account_intelligence.workflow.stage import Stage
    from tests.connectors.conftest import make_run
    from tests.fakes.grading_fakes import FakeLedger, FakeSources, ScriptedJudge
    from tests.grading.test_integration import Clock, Proposals, _rebind
    from tests.grading.world import SNIPPET, SNIPPET_TEXT

    base = load_grading_config()
    independent = base.model_copy(
        update={"judge": base.judge.model_copy(update={"generator_task": "extraction"})}
    )
    assert judge_profile_ok(independent) is True
    account, run, _ = make_run(tenant_a, domain="acme.example")
    _admin("UPDATE account_runs SET created_at = %s WHERE id = %s", NOW - timedelta(hours=3), run)
    _admin(
        "UPDATE accounts SET name = 'Acme', careers_url = 'https://careers.acme.example/',"
        " careers_url_verified_at = now(), careers_fetch_ref = 'synthetic-fetch' WHERE id = %s",
        account,
    )
    w = _rebind(world(), run)
    proposals = [
        sourced(
            "j",
            "jobs.salesforce_admin.open",
            "Senior Salesforce Administrator",
            (CAREERS, "Acme is hiring a Senior Salesforce Administrator to own the CRM platform."),
        ),
        sourced(
            "cto",
            "people.cto.name",
            "Jane Doe",
            (SNIPPET, SNIPPET_TEXT.split(": ", 1)[1]),
            confirming_question="Is Jane Doe the CTO?",
        ),
    ]
    deps = DistillGradeDeps(
        tenant_txn,
        FakeSources(w.sources),
        FakeLedger(w.fetches),
        Proposals(proposals),
        independent,
        Clock(),
        judge=ScriptedJudge([{"supported": True, "reason": "stated"}] * 4),
    )
    spec = next(s for s in load_spec().stages if s.id == "distill_grade")
    await Stage(spec, make_distill_grade_handler(deps)).run(
        {
            "tenant_id": str(tenant_a),
            "run_id": str(run),
            "references": {"claim_proposals": "proposals/run-1"},
        }
    )
    grades = dict(
        _admin(
            "SELECT attribute_key, product_grade FROM facts WHERE run_id = %s"
            " AND superseded_at IS NULL",
            run,
        )
    )
    assert grades["jobs.salesforce_admin.open"] == "confirmed"
    assert grades["people.cto.name"] == "lead"
    leads = _admin("SELECT lead_key, status FROM lead_verifications WHERE run_id = %s", run)
    assert len(leads) == 1 and leads[0][1] == "pending"
