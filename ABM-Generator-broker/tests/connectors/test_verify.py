"""Placeholders for provider facts that could not be sourced ([VERIFY] in docs/providers).

Each test is skipped until the fact is verified against the provider's first-party
documentation or the account's plan; then replace the skip with a real assertion
(contract test or live smoke) and update docs/providers/CLAIMS.md.
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="[VERIFY] provider fact not sourced; see docs/providers/CLAIMS.md"
)


def test_verify_p_search_retry_after_on_429() -> None: ...


def test_verify_p_search_failed_calls_billing() -> None: ...


def test_verify_p_scrape_plan_rate_limit() -> None: ...


def test_verify_p_scrape_request_id_idempotency_for_plain_scrapes() -> None: ...


def test_verify_p_scrape_usd_per_credit() -> None: ...


def test_verify_p_signals_rate_limit() -> None: ...


def test_verify_p_signals_usd_per_request() -> None: ...


def test_verify_p_signals_failed_calls_billing() -> None: ...


def test_verify_p_people_grid_output_contract() -> None: ...


def test_verify_p_people_cost_per_run() -> None: ...


def test_verify_p_org_retry_after_on_429() -> None: ...


def test_verify_p_org_usd_per_credit() -> None: ...
