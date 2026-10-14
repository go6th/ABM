"""The public origin a browser credential request must come from: public HTTPS anywhere; in dev
and test only, loopback HTTP on any port (the replica stack serves its web on 3010)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from account_intelligence.api.routes.auth import _allowed_base_url


@pytest.mark.parametrize(
    ("env", "base", "allowed"),
    [
        ("production", "https://app.example.com", True),
        ("production", "http://localhost:3000", False),
        ("dev", "http://localhost:3010", True),
        ("test", "http://127.0.0.1:3001", True),
        ("dev", "http://app.example.com", False),
        ("dev", "ftp://localhost", False),
    ],
)
def test_only_https_or_dev_loopback_origins_are_accepted(
    monkeypatch: pytest.MonkeyPatch, env: str, base: str, allowed: bool
) -> None:
    import account_intelligence.config as config

    monkeypatch.setattr(config, "get_settings", lambda: SimpleNamespace(app_env=env))
    assert _allowed_base_url(base) is allowed
