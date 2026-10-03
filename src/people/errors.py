"""Typed failures for the people pipeline."""

from account_intelligence.platform.ports import NotConfigured as PlatformNotConfigured


class PeopleError(RuntimeError):
    """Base class for expected people pipeline failures."""


class InvariantViolation(PeopleError):
    """A row-derived invariant of the people counts or statuses does not hold."""


class MergeError(PeopleError):
    """The merge plan is not stable, has a cycle, or points at a merged row."""


class SchemaViolation(PeopleError):
    """Untrusted structured output (extractor or provider record) failed validation."""


class ConfigError(PeopleError):
    """The people configuration is invalid, incomplete or unsourced."""


class NotConfigured(PeopleError, PlatformNotConfigured):
    """The production adapter for a people port does not exist yet."""
