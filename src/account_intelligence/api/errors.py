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
