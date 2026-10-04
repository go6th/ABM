"""Typed failures for the quality gate.

Guarantees: unavailable checks and inputs become explicit failures, never passes
(GUARANTEE(test_missing_legacy_checks_fail_closed)).
"""

from __future__ import annotations


class NotConfigured(RuntimeError):
    """A required gate adapter or policy has not been supplied."""

    def __init__(self, dependency: str) -> None:
        super().__init__(f"{dependency} is not configured")
        self.dependency = dependency


class GateInputError(RuntimeError):
    """A supplied gate input cannot be read or validated safely."""


class GateCheckError(RuntimeError):
    """A configured gate check failed to execute safely."""
