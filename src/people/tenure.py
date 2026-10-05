"""Tenure from the join date and the role start, at an injected ``as_of`` date (P7). Pure.

Tenure is measured from the join date when the profile gives one, otherwise from the role start
(the base is "profiles with a join or start date"). The date's precision is kept: a year-only or
month-only date gives a range of possible months, and a bucket is assigned only when the whole
range falls in one bucket (otherwise ``unknown`` with reason ``precision``). A future date, or a
role start before the join date, is ``date_invalid`` and never bucketed. Both ends of the range are
non-decreasing in ``as_of``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal

from people.config import TenureBucket
from people.models import PartialDate


@dataclass(frozen=True)
class Tenure:
    """The tenure verdict for one profile."""

    status: Literal["known", "unknown", "date_invalid"]
    basis: Literal["join_date", "role_start"] | None = None
    months_min: int | None = None
    months_max: int | None = None
    bucket: str | None = None
    reason: str | None = None


def months_between(start: date, end: date) -> int:
    """Whole months from ``start`` to ``end`` (0 when ``end`` is not after ``start``)."""
    months = (end.year - start.year) * 12 + end.month - start.month
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def bucket_of(months: int, buckets: Sequence[TenureBucket]) -> str:
    """The bucket containing ``months`` (buckets are contiguous from 0 and end open)."""
    for b in buckets:
        if months >= b.min_months and (b.max_months is None or months < b.max_months):
            return b.id
    raise ValueError("months below the first bucket")


def compute_tenure(
    join: PartialDate | None,
    role_start: PartialDate | None,
    as_of: date,
    buckets: Sequence[TenureBucket],
) -> Tenure:
    """The tenure verdict (see the module docstring)."""
    for value in (join, role_start):
        if value is not None and value.earliest() > as_of:
            return Tenure("date_invalid", reason="future_date")
    if join is not None and role_start is not None and role_start.latest() < join.earliest():
        return Tenure("date_invalid", reason="role_start_before_join")
    start = join if join is not None else role_start
    if start is None:
        return Tenure("unknown", reason="no_date")
    basis: Literal["join_date", "role_start"] = "join_date" if join is not None else "role_start"
    low = months_between(min(start.latest(), as_of), as_of)
    high = months_between(start.earliest(), as_of)
    first, last = bucket_of(low, buckets), bucket_of(high, buckets)
    if first != last:
        return Tenure("unknown", basis, low, high, reason="precision")
    return Tenure("known", basis, low, high, bucket=first)
