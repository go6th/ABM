"""Calendar based, deterministic posting window logic."""

from calendar import monthrange
from datetime import date
from typing import Literal

WindowBucket = Literal["in_window", "out_of_window", "undated"]


def months_before(as_of: date, months: int) -> date:
    """The same day ``months`` calendar months before ``as_of`` (clamped to the month's end):
    the news, posting and detection windows of config/skill/scope.yaml."""
    if months < 0:
        raise ValueError("months must not be negative")
    month = as_of.month - months
    year = as_of.year
    # LOOP(month normalisation): cap=months // 12 + 1 iterations; exit=month in 1..12
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, min(as_of.day, monthrange(year, month)[1]))


def window_start(as_of: date) -> date:
    """Return the inclusive date twelve calendar months before as_of."""
    return months_before(as_of, 12)


def classify_window(posting_date: date | None, as_of: date) -> WindowBucket:
    """Classify a date against the inclusive rolling calendar-month window."""
    if posting_date is None:
        return "undated"
    return "in_window" if window_start(as_of) <= posting_date <= as_of else "out_of_window"
