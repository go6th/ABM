"""Secrets from environment variables only (roadmap s11.4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.settings import LIVE_KEYS, Settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [*LIVE_KEYS, "ENV", "MODEL_MODE", "BROKER_MODE", "DATABASE_URL"]:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_offline() -> None:
    s = Settings()
    assert s.model_mode == "fake"
    assert s.broker_mode == "recorded"
    assert s.missing_live_keys() == list(LIVE_KEYS)


def test_keys_come_from_environment_and_never_print(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SUMBLE_API_KEY", "sumble-secret-value")
    s = Settings()
    assert s.sumble_api_key is not None
    assert s.sumble_api_key.get_secret_value() == "sumble-secret-value"
    assert "sumble-secret-value" not in repr(s)
    assert "sumble-secret-value" not in str(s.model_dump())
    assert "SUMBLE_API_KEY" not in s.missing_live_keys()


def test_dotenv_files_are_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-file\nENV=production\n")
    monkeypatch.chdir(tmp_path)
    s = Settings()
    assert s.anthropic_api_key is None
    assert s.env == "dev"


def test_unknown_environment_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "prod-ish")
    with pytest.raises(ValidationError):
        Settings()
