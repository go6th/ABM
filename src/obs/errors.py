"""Typed failures of the observability package."""

from __future__ import annotations


class ObsError(RuntimeError):
    """Base class."""


class CatalogInvalid(ObsError):
    """A telemetry catalog or the SLO, alert or dashboard config is invalid (startup stops)."""


class SourceMissing(ObsError):  # noqa: N818 - the spec's own name
    """A durable source a derivation needs does not exist (the result is NoData, never a guess)."""

    def __init__(self, name: str) -> None:
        super().__init__(f"source missing: {name}")
        self.name = name


class NotConfigured(ObsError):
    """A production telemetry adapter that does not exist (never a fake success)."""
