"""Tracker typed errors."""


class TrackerError(Exception):
    """Base class for tracker errors."""


class IllegalTransition(TrackerError):
    """The transition table refuses this move (5.2)."""


class NotOwner(TrackerError):
    """The actor may not write this row (T7)."""


class ProofMismatch(TrackerError):
    """A call reference is not a call of this tenant and run, or its record disagrees (T2)."""


class ProofIncomplete(TrackerError):
    """Evidence is missing something the rule needs (no calls and no reason)."""


class UnknownRow(TrackerError):
    """The run has no row with this key."""


class VersionConflict(TrackerError):
    """Another writer changed the row; re-read and re-evaluate (bounded)."""


class RunNotSeeded(TrackerError):
    """The run has no seeded tracker."""


class CompletionBlocked(TrackerError):
    """The verdict forbids COMPLETED."""


class ConfigError(TrackerError):
    """Tracker configuration is missing or invalid (fail fast at startup)."""


class UnsafeDetail(TrackerError):
    """Internal detail text names a broker, a credit figure or another account (T9)."""
