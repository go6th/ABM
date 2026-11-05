"""Typed API failures shared by request handlers.

Guarantees: callers receive stable problem identifiers without internal detail
(GUARANTEE(test_api_problem_shapes)); unavailable adapters fail closed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class APIProblem(Exception):
    """An expected, safe-to-return HTTP problem."""

    def __init__(
        self,
        status: int,
        type: str,
        title: str,
        detail: str,
        headers: Mapping[str, str] | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.type = type
        self.title = title
        self.detail = detail
        self.headers = dict(headers or {})
        # safe, structured context for the client (e.g. detected company candidates); never
        # document text, secrets or another tenant's data
        self.extra = dict(extra or {})


class NotConfigured(RuntimeError):
    """Raised when a production adapter required by an operation is not wired."""

    def __init__(self, dependency: str) -> None:
        super().__init__(f"{dependency} is not configured")
        self.dependency = dependency


def require_confirmed_twice(confirm: bool | None, confirm_again: bool | None) -> None:
    """428 ``confirm_twice`` unless a start request carries both confirmations
    (``runs.start_guard``: nothing paid starts without a person saying yes twice)."""
    from account_intelligence.runs.start_guard import (
        CONFIRM_TWICE_CODE,
        CONFIRM_TWICE_TEXT,
        CONFIRM_TWICE_TITLE,
        confirmed_twice,
    )

    if not confirmed_twice(confirm, confirm_again):
        raise APIProblem(428, CONFIRM_TWICE_CODE, CONFIRM_TWICE_TITLE, CONFIRM_TWICE_TEXT)
