"""Typed grading errors. Nothing in the grading layer fails with a bare KeyError or TypeError."""

from __future__ import annotations

from account_intelligence.platform.ports import NotConfigured as PlatformNotConfigured


class GradingError(Exception):
    """Base class. ``reason_code`` is stable and neutral (stored in claim_drops)."""

    reason_code = "grading_error"


class ConfigError(GradingError):
    reason_code = "config_error"


class UnknownTier(GradingError):
    reason_code = "unknown_tier"


class UnknownAttributeClass(GradingError):
    reason_code = "unknown_attribute_class"


class NaiveDatetime(GradingError):
    reason_code = "naive_datetime"


class SchemaViolation(GradingError):
    reason_code = "schema_violation"


class UrlNotInRun(GradingError):
    reason_code = "url_not_in_run"


class SpanNotFound(GradingError):
    reason_code = "unsupported_sentence"


class ValueNotInSentence(SpanNotFound):
    reason_code = "value_not_in_sentence"


class HostileContent(GradingError):
    reason_code = "hostile_source"


class NotPrimaryRead(GradingError):
    reason_code = "not_primary_read"


class LeadRuleViolation(GradingError):
    reason_code = "lead_rule_violation"


class JudgeUnavailable(GradingError):
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
