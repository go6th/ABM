"""The selectable model catalog fails closed on malformed or unpriced model facts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from account_intelligence.chat.registry import ChatConfigInvalid, load_chat_registry


def _catalog(tmp_path: Path) -> Path:
    """A small valid provider catalogue with exact pricing fields."""
    content: dict[str, Any] = {
        "version": 1,
        "default_model": "model-a",
        "limits": {
            "max_turns": 2,
            "max_input_chars": 500,
            "max_output_tokens": 20,
            "max_spend_usd_per_thread": "0.50",
        },
        "providers": {"anthropic": {"enabled": True, "credential_source": "env_or_secret_manager"}},
        "models": [
            {
                "id": "model-a",
                "provider": "anthropic",
                "display_name": "Model A",
                "input_usd_per_million": "1.0",
                "output_usd_per_million": "2.0",
                "cache_write_usd_per_million": "1.25",
                "cache_read_usd_per_million": "0.1",
                "context_window": 100,
                "max_output_tokens": 50,
                "capabilities": {"streaming": True, "vision": False, "tool_use": True},
            }
        ],
    }
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(content))
    return path


def test_catalog_exposes_public_facts_and_exact_decimal_costs() -> None:
    registry = load_chat_registry()
    model = registry.model(None)
    assert model.id == registry.default_model == "claude-sonnet-5-5"
    assert model.public()["provider"] == "anthropic"
    assert model.public()["capabilities"] == {
        "streaming": True,
        "vision": False,
        "tool_use": False,
    }
    assert model.public(output_limit=2048)["max_output_tokens"] == 2048
    assert model.cost(input_tokens=1_000_000, output_tokens=500_000) == 7
    with pytest.raises(ChatConfigInvalid, match="not available"):
        registry.model("unapproved-model")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: data.update(version=2), "version"),
        (lambda data: data.update(unexpected="value"), "unsupported fields"),
        (lambda data: data.update(default_model="missing"), "default_model"),
        (lambda data: data["models"][0].update(provider="openai"), "provider"),
        (lambda data: data["models"][0].update(input_usd_per_million="NaN"), "price"),
        (lambda data: data["models"][0].update(max_output_tokens=101), "context window"),
        (lambda data: data["models"][0].update(capabilities={"streaming": "yes"}), "capabilities"),
        (lambda data: data["limits"].update(max_turns=True), "positive integer"),
        (lambda data: data["providers"]["anthropic"].update(enabled=False), "enabled"),
    ],
)
def test_invalid_registry_is_rejected(tmp_path: Path, change: Any, message: str) -> None:
    path = _catalog(tmp_path)
    data = yaml.safe_load(path.read_text())
    change(data)
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ChatConfigInvalid, match=message):
        load_chat_registry(path)


def test_duplicate_model_ids_and_context_output_limits_are_rejected(tmp_path: Path) -> None:
    path = _catalog(tmp_path)
    data = yaml.safe_load(path.read_text())
    duplicate = dict(data["models"][0])
    data["models"].append(duplicate)
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ChatConfigInvalid, match="duplicate"):
        load_chat_registry(path)

    data["models"] = data["models"][:1]
    data["limits"]["max_output_tokens"] = 51
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ChatConfigInvalid, match="selectable model"):
        load_chat_registry(path)
