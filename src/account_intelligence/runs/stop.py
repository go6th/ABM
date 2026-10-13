"""Why a run stopped: a bounded, redacted description and the "anything usable" test.

``describe`` turns an exception into ``<ErrorClass>: <message>`` with every secret-shaped token
removed (API keys, bearer tokens, long opaque strings, URLs with credentials) and the result cut
to the column's bound. ``has_usable_output`` decides between PARTIAL and FAILED after an
unexpected stop: a run that holds at least one finished paid call, one fact or one deliverable
has something a person can use, so it ends PARTIAL and keeps its standing; FAILED is only for a
run with nothing.

GUARANTEE(test_stop_detail_never_carries_a_secret)
GUARANTEE(test_an_unexpected_error_ends_partial_when_anything_is_stored)
"""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MAX_DETAIL = 600
_SECRETS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),  # provider keys of the sk- family
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*\S+"),
    re.compile(r"(?i)x-api-key:\s*\S+"),
    re.compile(r"://[^/\s:@]+:[^/\s@]+@"),  # user:password@ in a URL
    re.compile(r"\b[A-Za-z0-9_-]{40,}\b"),  # long opaque tokens
)


def redact(message: str) -> str:
    """The message with secret-shaped tokens replaced by ``[redacted]``."""
    out = message
    for pattern in _SECRETS:
        out = pattern.sub("[redacted]", out)
    return out


def describe(error: BaseException, *, limit: int = MAX_DETAIL) -> str:
    """``<ErrorClass>: <redacted message>`` bounded to ``limit`` characters."""
    message = " ".join(str(error).split())
    body = f"{type(error).__name__}: {redact(message)}" if message else type(error).__name__
    return body[:limit]


async def has_usable_output(session: AsyncSession, run_id: UUID) -> bool:
    """True when the run holds a finished paid call with a stored payload, a fact, or a
    deliverable (``session`` is the run's tenant transaction)."""
    found: object = (
        await session.execute(
            text(
                "SELECT EXISTS (SELECT 1 FROM tool_calls WHERE run_id = :r"
                " AND response_ref IS NOT NULL)"
                " OR EXISTS (SELECT 1 FROM facts WHERE run_id = :r)"
                " OR EXISTS (SELECT 1 FROM deliverables WHERE run_id = :r)"
            ),
            {"r": run_id},
        )
    ).scalar_one()
    return bool(found)
