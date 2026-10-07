"""Inbound monitor webhooks are hostile (SM2) and outbound alert webhooks are signed (AL6): one
signing scheme, HMAC-SHA256 over ``<timestamp>.<body>``, a 5-minute tolerance and event ids for
deduplication. Pure (keys and the clock are passed in).

Inbound: unknown source rejected, bad or stale signature rejected, a seen event id is a replay.
Nothing in the payload is trusted as a fact, an instruction or a URL to fetch; it is stored and
handed to the tool-less extractor.

GUARANTEE(test_webhook_signatures_tolerance_and_replays)
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Container, Mapping
from typing import Literal

TOLERANCE_S = 300  # source: assets/alert_rules.yaml "5-minute tolerance"
Verdict = Literal["ok", "unknown_source", "bad_signature", "stale", "replay"]


def sign(key: bytes, timestamp: int, body: bytes) -> str:
    """``v1=<hex>`` over ``<timestamp>.<body>``."""
    mac = hmac.new(key, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"v1={mac}"


def verify(
    *,
    keys: Mapping[str, bytes],
    source: str,
    timestamp: int,
    body: bytes,
    signature: str,
    event_id: str,
    now: int,
    seen: Container[str],
) -> Verdict:
    """The verdict on an inbound delivery (checks in a fixed order; constant-time compare)."""
    key = keys.get(source)
    if key is None:
        return "unknown_source"
    if abs(now - timestamp) > TOLERANCE_S:
        return "stale"
    if not hmac.compare_digest(sign(key, timestamp, body), signature):
        return "bad_signature"
    if f"{source}:{event_id}" in seen:
        return "replay"
    return "ok"


def retry_schedule(
    *, first_delay_s: int = 60, factor: int = 2, window_s: int = 24 * 3600
) -> list[int]:
    """Exponential retry offsets (seconds after the first attempt) within the 24 h window
    (source: "exponential retries for 24 h"; first delay and factor are proposals)."""
    out, delay, t = [], first_delay_s, 0
    while t + delay <= window_s:  # LOOP(webhook retry schedule): cap=window; exit=window end
        t += delay
        out.append(t)
        delay *= factor
    return out
