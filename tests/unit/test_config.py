"""Unit tests for config defaults and the logging field allow-list."""

from __future__ import annotations

import pytest

from abm_generator.config import Settings
from abm_generator.logging_setup import ALLOWED_FIELDS, _allowlist_processor


def test_settings_defaults_are_dev_safe() -> None:
    s = Settings(_env_prefix="", _env_file=None)  # type: ignore[call-arg]
    assert s.db_pool_size == 5
    assert s.slice_seconds == 240
    assert s.max_slices == 60
    assert "app_rw" in s.database_url  # runtime role, never postgres


def test_allowlist_processor_drops_unknown_fields() -> None:
    out = _allowlist_processor(None, "info", {"event": "x", "prompt": "SECRET", "run_id": "r"})
    assert "prompt" not in out
    assert out["event"] == "x"
    assert out["run_id"] == "r"


def test_allowlist_covers_core_fields() -> None:
    for field in ("event", "run_id", "tenant_id", "tool", "status"):
        assert field in ALLOWED_FIELDS


@pytest.mark.parametrize("field", ["prompt", "document_text", "api_key", "token_count_raw"])
def test_private_fields_are_never_allowed(field: str) -> None:
    assert field not in ALLOWED_FIELDS
