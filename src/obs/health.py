"""Heartbeat and watchdog (O10): every service records a heartbeat; ``silent`` (pure) names the
services whose last heartbeat is older than the allowed silence, so missing data raises an alert,
never a green board. The watchdog reads only the drop counter and heartbeats, not the pipeline it
watches.

GUARANTEE(test_silence_is_an_alert): O10.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime


def silent(
    heartbeats: Mapping[str, datetime], services: Iterable[str], now: datetime, allowed_s: float
) -> list[str]:
    """Services with no heartbeat or one older than ``allowed_s``."""
    out = []
    for svc in sorted(services):
        last = heartbeats.get(svc)
        if last is None or (now - last).total_seconds() > allowed_s:
            out.append(svc)
    return out


def watchdog(drops: Mapping[str, int], previous: Mapping[str, int], limit: int) -> list[str]:
    """Signals whose drop count grew by more than ``limit`` since the previous check."""
    return sorted(s for s, n in drops.items() if n - previous.get(s, 0) > limit)
