"""Typed grading errors. Nothing in the grading layer fails with a bare KeyError or TypeError."""

from __future__ import annotations

from account_intelligence.platform.ports import NotConfigured as PlatformNotConfigured


class GradingError(Exception):
    """Base class. ``reason_code`` is stable and neutral (stored in claim_drops)."""

    reason_code = "grading_error"


class ConfigError(GradingError):
    """Grading configuration is missing or invalid."""

    reason_code = "config_error"


class UnknownTier(GradingError):
    """A tier not in the configuration (F2)."""

    reason_code = "unknown_tier"


class UnknownAttributeClass(GradingError):
    """An attribute class not in the configuration (F2)."""

    reason_code = "unknown_attribute_class"


class NaiveDatetime(GradingError):
    """A timezone-naive datetime (F2, F5: time is always aware)."""

    reason_code = "naive_datetime"


class SchemaViolation(GradingError):
    """A proposal or graded claim breaks the schema (G1, G2, G8)."""

    reason_code = "schema_violation"


class UrlNotInRun(GradingError):
    """A cited URL was not returned by any call of this run (G3)."""

    reason_code = "url_not_in_run"


class SpanNotFound(GradingError):
    """The supporting sentence is not verbatim in the cited source (G4)."""

    reason_code = "unsupported_sentence"


class ValueNotInSentence(SpanNotFound):
    """The claimed value does not appear in its supporting sentence (G4)."""

    reason_code = "value_not_in_sentence"


class HostileContent(GradingError):
    """A canary or an instruction was echoed: the source is hostile (G14)."""

    reason_code = "hostile_source"


class NotPrimaryRead(GradingError):
    """Confirmed needs a code-verified primary read this run (G5)."""

    reason_code = "not_primary_read"


class LeadRuleViolation(GradingError):
    """The Lead rule refused a transition (G9)."""

    reason_code = "lead_rule_violation"


class JudgeUnavailable(GradingError):
    """The judge could not answer (claims are capped, G12)."""

    reason_code = "judge_unavailable"


class NotConfigured(GradingError, PlatformNotConfigured):
    """A production adapter that does not exist yet. The claim is never granted A, B or
    Confirmed on its behalf (G12)."""

    reason_code = "not_configured"


def require_aware(value: object, what: str) -> None:
    """Raise ``NaiveDatetime`` unless ``value`` is a timezone-aware datetime (F2, F5)."""
    from datetime import datetime

    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise NaiveDatetime(f"{what} must be a timezone-aware datetime")
