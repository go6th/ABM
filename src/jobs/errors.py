"""Typed failures for the job research pipeline."""

from account_intelligence.platform.ports import NotConfigured as PlatformNotConfigured


class JobsError(RuntimeError):
    """Base class for expected job pipeline failures."""


class InvariantViolation(JobsError):
    """Row-derived pipeline invariants do not hold."""


class WindowError(JobsError):
    """A posting date cannot be interpreted safely."""


class SchemaViolation(JobsError):
    """Untrusted structured output failed validation."""


class ConfigError(JobsError):
    """Pipeline configuration is invalid or incomplete."""


class NotConfigured(JobsError, PlatformNotConfigured):
    """The production adapter has not been configured."""
