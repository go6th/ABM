"""Account-to-CRM mapping (CW3): only a human-confirmed mapping is used; an ambiguous or missing
candidate set writes nothing. Pure.

GUARANTEE(test_ambiguous_or_unconfirmed_mappings_write_nothing)
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

Resolution = Literal["mapped", "ambiguous", "missing", "unconfirmed"]


def resolve(candidates: Sequence[str], confirmed: str | None) -> tuple[Resolution, str | None]:
    """The record to write to, or why none."""
    if confirmed is not None:
        return ("mapped", confirmed) if confirmed in candidates else ("unconfirmed", None)
    if not candidates:
        return "missing", None
    if len(candidates) > 1:
        return "ambiguous", None
    return "unconfirmed", None  # one candidate still needs a human confirmation
