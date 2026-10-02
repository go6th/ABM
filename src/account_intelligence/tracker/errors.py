"""Tracker typed errors."""


class TrackerError(Exception):
    """Base class for tracker errors."""


class IllegalTransition(TrackerError):
    pass


class NotOwner(TrackerError):
    pass


class ProofMismatch(TrackerError):
    pass


class ProofIncomplete(TrackerError):
    pass


class UnknownRow(TrackerError):
    pass


class VersionConflict(TrackerError):
    pass


class RunNotSeeded(TrackerError):
    pass


class CompletionBlocked(TrackerError):
    pass


class ConfigError(TrackerError):
    pass
