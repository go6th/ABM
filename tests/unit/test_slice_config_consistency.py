"""The three slice timings agree: a slice (``workflow_v1.yaml``) finishes with margin inside the
Cloud Tasks dispatch deadline (``CloudTasksScheduler.SLICE_DEADLINE_S``, at most the service's
1800 s maximum), and a provider-call lease (``providers.yaml``) outlives a slice so a call still
running at the slice end is not re-leased by the next slice (Tolmar, 2026-10-21).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from account_intelligence.platform.gcp.adapters import CloudTasksScheduler

CONFIG = Path(__file__).resolve().parents[2] / "config"
CHECKPOINT_MARGIN_S = 60  # the checkpoint and lease release after the slice's last call
CLOUD_TASKS_MAX_DEADLINE_S = 1800


def _slice_seconds() -> int:
    spec = yaml.safe_load((CONFIG / "workflow_v1.yaml").read_text())
    return int(spec["settings"]["slice_seconds"])


def _lease_ttl_s() -> int:
    providers = yaml.safe_load((CONFIG / "providers.yaml").read_text())
    return int(providers["defaults"]["ledger"]["lease_ttl_s"])


def test_a_slice_fits_inside_the_dispatch_deadline_with_margin() -> None:
    deadline = CloudTasksScheduler.SLICE_DEADLINE_S
    assert _slice_seconds() + CHECKPOINT_MARGIN_S <= deadline <= CLOUD_TASKS_MAX_DEADLINE_S


def test_a_provider_lease_outlives_a_slice() -> None:
    assert _lease_ttl_s() > _slice_seconds()


def _ledger() -> dict[str, float]:
    providers = yaml.safe_load((CONFIG / "providers.yaml").read_text())
    return dict(providers["defaults"]["ledger"])


ROOT = Path(__file__).resolve().parents[2]


def _runs_queue() -> dict[str, int]:
    """The ``runs`` queue's retry numbers, read from ``infra/stack/main.tf`` (seconds)."""
    text = (ROOT / "infra" / "stack" / "main.tf").read_text()
    block = re.search(r"\n    runs = \{(.*?)\n    \}", text, re.S)
    assert block, "the runs queue is not in infra/stack/main.tf"
    body = re.sub(r"#[^\n]*", "", block.group(1))  # comments carry numbers too
    found = {
        key: int(re.search(rf"{key}\s*=\s*\"?(\d+)", body).group(1))  # type: ignore[union-attr]
        for key in ("max_attempts", "min_backoff", "max_backoff", "max_doublings")
    }
    return found


def retry_times(
    max_attempts: int, min_backoff: int, max_backoff: int, max_doublings: int
) -> list[int]:
    """Seconds after the first failure at which Cloud Tasks makes each retry. The gap starts at
    ``min_backoff``, doubles ``max_doublings`` times, then grows by ``min_backoff * 2 **
    max_doublings`` each time, and never passes ``max_backoff`` (the Cloud Tasks rule). A failed
    attempt is a 5xx answer, including the 503 "busy"; ``max_attempts`` counts the first one."""
    times: list[int] = []
    gap, at = min_backoff, 0
    for retry in range(max_attempts - 1):
        at += min(gap, max_backoff)
        times.append(at)
        gap = gap * 2 if retry < max_doublings else gap + min_backoff * 2**max_doublings
    return times


def test_the_retry_schedule_of_the_runs_queue() -> None:
    """Eight attempts (owner decision 2026-10-30; five before): retries at about +10, +30, +70,
    +150 s, then +310, +610 and +910 s."""
    queue = _runs_queue()
    assert queue["max_attempts"] == 8
    assert retry_times(**queue) == [10, 30, 70, 150, 310, 610, 910]
    # the same function reproduces the five-attempt schedule the first design was sized for
    assert retry_times(5, 10, 300, 4) == [10, 30, 70, 150]


def test_a_killed_workers_lease_can_be_taken_by_the_last_retry_of_its_slice() -> None:
    """Orchestration review F5: the runner renews the lease every heartbeat; the stale time is
    longer than four missed beats (a live owner is never taken from). A dead owner's slice is
    taken over by the first retry made after the lease went stale (a beat may have been written
    just before the death), and the queue still has retries left if that one fails too."""
    ledger = _ledger()
    assert ledger["lease_stale_after_s"] >= 4 * ledger["lease_heartbeat_s"]
    times = retry_times(**_runs_queue())
    stale_at = ledger["lease_stale_after_s"] + ledger["lease_heartbeat_s"]
    takeover = next(t for t in times if t > stale_at)
    assert takeover == 150  # the retries before it (+10, +30, +70 s) answer "busy"
    assert [t for t in times if t < takeover] == [10, 30, 70]
    # the retries are not used up by the takeover: three more remain (+310, +610, +910 s)
    assert len([t for t in times if t > takeover]) >= 3
