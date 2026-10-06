"""Trace identity (pure; O2).

* ``trace_id_for(run_id)``: the run UUID's 128-bit integer (its 32 hex digits): total and
  injective on UUIDs, the same in the API, every slice, retry and resume. Not a UUID: a sha256
  prefix of the string (documented; injective in practice, not proven).
* ``span_id_for(*parts)``: a deterministic, non-zero 64-bit span id from (run, stage, attempt,
  unit key...), so a crash and resume re-emits the same span id instead of a duplicate.
* ``traceparent`` encode and decode (W3C form ``00-<trace>-<span>-<flags>``): identifiers only.

GUARANTEE(test_trace_ids_are_total_and_injective): property test.
"""

from __future__ import annotations

import hashlib
import re
from uuid import UUID

TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def trace_id_for(run_id: UUID | str) -> int:
    """The run's trace id."""
    if isinstance(run_id, UUID):
        value = run_id.int
    else:
        try:
            value = UUID(run_id).int
        except ValueError:
            value = int.from_bytes(hashlib.sha256(run_id.encode()).digest()[:16], "big")
    return value or 1  # the all-zero trace id is invalid in W3C trace context


def span_id_for(*parts: object) -> int:
    """Deterministic non-zero span id."""
    blob = "\x1f".join(str(p) for p in parts).encode()
    return int.from_bytes(hashlib.sha256(blob).digest()[:8], "big") or 1


def encode(trace_id: int, span_id: int, sampled: bool = True) -> str:
    """A ``traceparent`` header value."""
    return f"00-{trace_id:032x}-{span_id:016x}-{'01' if sampled else '00'}"


def decode(header: str) -> tuple[int, int, bool] | None:
    """``(trace_id, span_id, sampled)`` or None when malformed or all-zero."""
    m = TRACEPARENT.match(header.strip().lower())
    if m is None:
        return None
    trace_id, span_id = int(m.group(1), 16), int(m.group(2), 16)
    if trace_id == 0 or span_id == 0:
        return None
    return trace_id, span_id, m.group(3) == "01"
