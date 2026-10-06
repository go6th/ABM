"""A synthetic routing config with two families and known windows (test only; no real ids)."""

from __future__ import annotations

from typing import Any

import pytest

from llm.config import RoutingConfig, build


def candidate(
    family: str,
    model: str,
    window: int | None = 1000,
    retention: bool | None = None,
    caching: bool | None = True,
    classes: tuple[str, ...] = ("public_web",),
    price: tuple[str | None, str | None] = ("1.0", "2.0"),
) -> dict[str, Any]:
    return {
        "adapter": "fake",
        "provider_code": "m_test",
        "family": {"value": family, "source": "synthetic"},
        "model": {"value": model, "source": "synthetic"},
        "context_window": {"value": window, "status": "synthetic"},
        "data_classes": {"value": list(classes), "status": "synthetic"},
        "zero_retention": {"value": retention, "status": "synthetic"},
        "caching": {"value": caching, "status": "synthetic"},
        "price": {
            "input_usd_per_mtok": price[0],
            "output_usd_per_mtok": price[1],
            "source": "synthetic",
        },
    }


def raw_config(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": "t",
        "routing_version": "rt",
        "benchmark_report": {"value": None, "status": "synthetic"},
        "task_kinds": {
            "status": "synthetic",
            "value": {
                "extract_facts": {
                    "tier": "extractor",
                    "max_tokens": 50,
                    "temperature": None,
                    "cacheable": True,
                },
                "support_judge": {
                    "tier": "judge",
                    "max_tokens": 50,
                    "temperature": None,
                    "cacheable": False,
                },
                "write_narrative": {
                    "tier": "planner",
                    "max_tokens": 50,
                    "temperature": None,
                    "cacheable": True,
                },
            },
        },
        "tiers": {
            "status": "synthetic",
            "judge_tier": "judge",
            "value": {
                "extractor": {"ladder": ["small", "big"], "fallback_after_retryable": 2},
                "planner": {"ladder": ["big"], "fallback_after_retryable": 1},
                "judge": {"ladder": ["big", "other"], "fallback_after_retryable": 1},
            },
        },
        "candidates": {
            "small": candidate("fam_a", "synthetic-small", window=1000),
            "big": candidate("fam_a", "synthetic-big", window=8000),
            "other": candidate("fam_b", "synthetic-other", window=8000, price=(None, None)),
        },
        "token_caps": {"status": "synthetic", "value": {"standard": 100000, "deep": None}},
        "health": {"status": "synthetic", "failure_threshold": 2, "window_s": 60, "cooldown_s": 30},
        "cache_read_ratio_floor": {"value": 0.7, "source": "ARCH"},
        "schema_reasks": {"value": 1, "source": "spec"},
    }
    data.update(over)
    return data


@pytest.fixture
def cfg() -> RoutingConfig:
    return build(raw_config(), b"synthetic")
