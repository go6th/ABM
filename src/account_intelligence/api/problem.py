"""Typed problem responses for the public API.

Guarantees: failures expose a stable public code and request id, never exception text.
GUARANTEE(test_problem_details_hide_internal_text).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DependencyUnavailable(Exception):
    """A required identity or application dependency is unavailable."""


TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    422: "Unprocessable Content",
    429: "Too Many Requests",
    500: "Internal Server Error",
    503: "Service Unavailable",
}


def problem_body(status: int, request_id: str, detail: str | None = None) -> dict[str, object]:
    """Return the standard problem-details fields with bounded, catalogued public detail."""
    title = TITLES.get(status, "Error")
    safe_detail = detail if detail in {"invalid token", "forbidden", "not found"} else title
    return {
        "type": f"urn:6th:problem:{status}",
        "title": title,
        "status": status,
        "detail": safe_detail,
        "request_id": request_id,
    }
