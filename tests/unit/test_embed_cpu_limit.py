"""The embedding pool is sized by the container's CPU quota, not the host's core count.

Staging 2026-10-22: a 4 vCPU worker loaded the embedder with 8 threads (``os.cpu_count()`` shows
the host), starving the event loop and the scan sidecar."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from documents import embed


def _fake_paths(monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> None:
    real = Path.read_text

    def read_text(self: Path, *a: Any, **k: Any) -> str:
        if str(self) in files:
            return files[str(self)]
        if str(self).startswith("/sys/fs/cgroup"):
            raise FileNotFoundError(str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", read_text)


def test_a_cgroup_v2_quota_caps_the_cpu_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    _fake_paths(monkeypatch, {"/sys/fs/cgroup/cpu.max": "400000 100000\n"})
    assert embed.cpu_limit() == 4
    assert embed.embed_threads() == 3  # one core stays free for the loop and the sidecar


def test_a_fractional_quota_rounds_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 8)
    _fake_paths(monkeypatch, {"/sys/fs/cgroup/cpu.max": "150000 100000\n"})
    assert embed.cpu_limit() == 2 and embed.embed_threads() == 1


def test_cgroup_v1_is_read_when_v2_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 16)
    _fake_paths(
        monkeypatch,
        {
            "/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "200000\n",
            "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000\n",
        },
    )
    assert embed.cpu_limit() == 2


def test_no_quota_falls_back_to_the_host_count_and_never_exceeds_eight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 32)
    _fake_paths(monkeypatch, {"/sys/fs/cgroup/cpu.max": "max 100000\n"})
    assert embed.cpu_limit() == 32 and embed.embed_threads() == 8


def test_one_cpu_still_gets_one_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 1)
    _fake_paths(monkeypatch, {})
    assert embed.embed_threads() == 1
