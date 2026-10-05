"""Synthetic checks for the public problem-details envelope."""

import pytest

from account_intelligence.api.pagination import InvalidCursor, decode_cursor, encode_cursor
from account_intelligence.api.problem import problem_body


def test_problem_details_hide_internal_text() -> None:
    """GUARANTEE(test_problem_details_hide_internal_text): errors never echo supplied text."""
    result = problem_body(500, "req-1", "password=secret database broke")
    assert result == {
        "type": "urn:6th:problem:500",
        "title": "Internal Server Error",
        "status": 500,
        "detail": "Internal Server Error",
        "request_id": "req-1",
    }


def test_known_auth_detail_is_safe() -> None:
    """A small fixed authentication message remains useful to callers."""
    assert problem_body(401, "req-2", "invalid token")["detail"] == "invalid token"


def test_signed_cursor_is_tenant_and_collection_bound() -> None:
    """GUARANTEE(test_signed_cursor_is_tenant_bound): signed cursors cannot cross scope."""
    cursor = encode_cursor(
        tenant_id="tenant-a", collection="runs", position=["2026-01-01", "run-1"], secret=b"s" * 32
    )
    assert decode_cursor(cursor, tenant_id="tenant-a", collection="runs", secret=b"s" * 32) == [
        "2026-01-01",
        "run-1",
    ]
    with pytest.raises(InvalidCursor):
        decode_cursor(cursor, tenant_id="tenant-b", collection="runs", secret=b"s" * 32)
    with pytest.raises(InvalidCursor):
        decode_cursor(cursor[:-1] + "x", tenant_id="tenant-a", collection="runs", secret=b"s" * 32)
