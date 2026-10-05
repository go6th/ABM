"""P0-2 and P0-4 harnesses on synthetic data: the golden format validates and scores a run; the
benchmark reports per arm with bases and says Blocked without reviewed accounts."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.golden_accounts import GoldenAccount, load_accounts, score_run
from evals.model_benchmark import report

GOLDEN = Path(__file__).resolve().parents[2] / "evals/golden/accounts"


def test_golden_accounts_validate_and_score() -> None:
    (acme,) = load_accounts(GOLDEN)
    assert acme.synthetic
    score = score_run(
        acme,
        {"prior_customer_status": "not_done", "job_postings_12_months": "done"},
        [
            {
                "attribute_key": "jobs.salesforce.build",
                "value": "salesforce",
                "product_grade": "confirmed",
            }
        ],
        ["https://profiles.example/acme-cto/"],
    )
    assert score.coverage_match == (2, 3)
    assert score.attributes_found == (1, 1) and score.attributes_grade_ok == (1, 1)
    assert score.people_found == (1, 1)
    with pytest.raises(ValidationError):
        GoldenAccount.model_validate(
            {**acme.model_dump(mode="json"), "coverage": {"no_such_row": "done"}}
        )


def test_benchmark_reports_per_arm_and_is_blocked_on_synthetic_data() -> None:
    runs = [
        {
            "arm": arm,
            "account_domain": "acme.example",
            "statuses": {"prior_customer_status": "not_done"},
            "facts": [],
            "people": [],
            "tokens": {"planner": 10},
        }
        for arm in ("single", "split")
    ]
    text = report(runs, GOLDEN)
    assert "Blocked" in text and "| single | 1 | 1 of 3 |" in text and "| split | 1 |" in text
    assert "no confirmed price" in text


def test_people_comparison_counts_with_bases_and_the_switch_filters_the_stage() -> None:
    from evals.people_compare import compare

    a = [
        {"profile_url": "https://p.example/x/", "title": "VP Engineering"},
        {"profile_url": "https://p.example/y", "title": "Director of IT Audit"},
    ]
    b = [
        {"profile_url": "https://p.example/x", "title": "VP Engineering"},
        {"name": "Sam Doe", "title": "Chief Information Officer"},
    ]
    c = compare(a, b)
    assert (c.a_found, c.b_found, c.overlap, c.only_a, c.only_b) == (2, 2, 1, 1, 1)
    assert (c.a_in_scope, c.b_in_scope) == (1, 2)  # IT audit is an excluded function

    from account_intelligence.workflow.handlers import people_operations

    assert people_operations() == {"people_by_technology", "org_teams"}
