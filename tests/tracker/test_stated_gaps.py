"""2026-11-05 hardening (staging run 8ed9911a): the graph and the gate passed, yet the run ended
PARTIAL on rows only the client can unblock, and the jobs read row said "Execution cap reached"
with every cap off. Rows the client must unblock are stated gaps; real failures still block; a
jobs row names its own reason."""

from __future__ import annotations

from account_intelligence.tracker.completion import RowState, evaluate
from account_intelligence.tracker.config import load_tracker_config
from account_intelligence.tracker.derive import derive_outcome
from account_intelligence.tracker.model import CallRecord, ReasonCode, Status

POLICY = load_tracker_config().d_12_completion_policy


def _row(key: str, status: Status, reason: ReasonCode | None) -> RowState:
    return RowState(key, "high", status, reason, "proof", False)


def test_rows_only_the_client_can_unblock_complete_with_gaps() -> None:
    rows = [
        _row("company_overview", Status.DONE, None),
        _row("people_by_platform", Status.NOT_DONE, ReasonCode.CONSENT_REQUIRED),
        _row("full_profiles_senior_people", Status.NOT_DONE, ReasonCode.CONSENT_REQUIRED),
        _row("job_board_by_lane", Status.NOT_DONE, ReasonCode.CAREERS_SITE_NOT_FOUND),
        _row("tech_stack", Status.PARTIAL, ReasonCode.PULL_NOT_APPROVED),
        _row("prior_customer_status", Status.NOT_DONE, ReasonCode.NO_INTERNAL_ACCESS),
    ]
    verdict = evaluate(rows, POLICY)
    assert verdict.can_complete and verdict.level == "complete_with_gaps"


def test_real_failures_still_block() -> None:
    for reason in (
        ReasonCode.SOURCE_FAILED,
        ReasonCode.NOT_AVAILABLE_YET,  # also a row the research never reached
        ReasonCode.POSTINGS_NOT_READ,
        ReasonCode.FEED_INCOMPLETE,
        ReasonCode.COUNTS_INCONSISTENT,
        ReasonCode.CAP_REACHED,
    ):
        verdict = evaluate([_row("job_postings_read_classified", Status.PARTIAL, reason)], POLICY)
        assert not verdict.can_complete, reason


def test_every_new_reason_has_client_text() -> None:
    texts = load_tracker_config().reason_codes
    for reason in (
        ReasonCode.POSTINGS_NOT_READ,
        ReasonCode.FEED_INCOMPLETE,
        ReasonCode.COUNTS_INCONSISTENT,
        ReasonCode.CAREERS_SITE_NOT_FOUND,
    ):
        assert texts[reason.value]


def test_a_reporter_names_its_own_partial_reason() -> None:
    calls = [CallRecord(call_ref="c1", tool_category="job_board", success=True, result_count=5)]
    own = derive_outcome(1, calls, True, True, None, partial_reason=ReasonCode.POSTINGS_NOT_READ)
    assert (own.status, own.reason) == (Status.PARTIAL, ReasonCode.POSTINGS_NOT_READ)
    default = derive_outcome(1, calls, True, True, None)
    assert default.reason == ReasonCode.CAP_REACHED  # other reporters are unchanged
