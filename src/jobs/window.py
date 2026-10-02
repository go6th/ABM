"""Calendar based, deterministic posting window logic."""

from calendar import monthrange
from datetime import date
from typing import Literal

WindowBucket = Literal["in_window", "out_of_window", "undated"]


def window_start(as_of: date) -> date:
    """Return the inclusive date twelve calendar months before as_of."""
    month = as_of.month - 12
    year = as_of.year
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, min(as_of.day, monthrange(year, month)[1]))


def classify_window(posting_date: date | None, as_of: date) -> WindowBucket:
    """Classify a date against the inclusive rolling calendar-month window."""
    if posting_date is None:
        return "undated"
    return "in_window" if window_start(as_of) <= posting_date <= as_of else "out_of_window"
