"""The three slice timings agree: a slice (``workflow_v1.yaml``) finishes with margin inside the
Cloud Tasks dispatch deadline (``CloudTasksScheduler.SLICE_DEADLINE_S``, at most the service's
1800 s maximum), and a provider-call lease (``providers.yaml``) outlives a slice so a call still
running at the slice end is not re-leased by the next slice (Tolmar, 2026-10-21).
"""

from __future__ import annotations

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
