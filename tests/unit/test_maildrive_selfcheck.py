"""The Mail and drive environment self-check (scripts/maildrive_selfcheck.py), offline: missing
OAuth apps and an http cloud redirect are reported with a fix; a configured environment passes;
secrets are never printed."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _module() -> Any:
    spec = importlib.util.spec_from_file_location(
        "maildrive_selfcheck", ROOT / "scripts" / "maildrive_selfcheck.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for provider in ("GOOGLE", "MICROSOFT"):
        for suffix in ("ID", "SECRET", "SECRET_FILE"):
            monkeypatch.delenv(f"MAILDRIVE_{provider}_CLIENT_{suffix}", raising=False)


def test_nothing_configured_is_not_ready(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _clear(monkeypatch)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    assert _module().main(["--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    failed = {c["name"] for c in out["checks"] if not c["ok"]}
    assert "redirect URI" in failed
    assert any(name.startswith("google OAuth app") for name in failed)
    assert all(c["fix"] for c in out["checks"] if not c["ok"])


def test_a_configured_cloud_environment_is_ready_and_never_prints_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _clear(monkeypatch)
    secret_file = tmp_path / "ms-secret"
    secret_file.write_text("ms-very-secret-value\n")
    monkeypatch.setenv("MAILDRIVE_GOOGLE_CLIENT_ID", "google-client.apps.example")
    monkeypatch.setenv("MAILDRIVE_GOOGLE_CLIENT_SECRET", "g-very-secret-value")
    monkeypatch.setenv("MAILDRIVE_MICROSOFT_CLIENT_ID", "00000000-aaaa-bbbb-cccc-000000000000")
    monkeypatch.setenv("MAILDRIVE_MICROSOFT_CLIENT_SECRET_FILE", str(secret_file))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example.com")
    assert _module().main(["--cloud"]) == 0
    printed = capsys.readouterr().out
    assert "READY" in printed and "NOT READY" not in printed
    assert "https://app.example.com/v1/mail-and-drive/callback" in printed
    assert "very-secret-value" not in printed


def test_an_http_or_wildcard_cloud_origin_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://136-81-60-29.sslip.io")
    assert _module().main(["--cloud", "--json"]) == 1
    names = {c["name"] for c in json.loads(capsys.readouterr().out)["checks"] if not c["ok"]}
    assert "redirect domain" in names
