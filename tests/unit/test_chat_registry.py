"""The selectable model catalog fails closed on malformed or unpriced model facts. The catalogue
names model ids; their provider, name, context window and prices come from ``models.yaml``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from account_intelligence.chat.registry import ChatConfigInvalid, load_chat_registry
from account_intelligence.config import CONFIG_DIR


def _catalog(tmp_path: Path) -> Path:
    """A small valid chat catalogue and, beside it, the models.yaml that prices its model."""
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
                "max_output_tokens": 50,
                "capabilities": {"streaming": True, "vision": False, "tool_use": True},
            }
        ],
    }
    models: dict[str, Any] = {
        "models": {
            "model-a": {
                "provider": "anthropic",
                "display_name": "Model A",
                "context_window": 100,
                "pricing": {
                    "input_usd_per_million": "1.0",
                    "cached_read_usd_per_million": "0.1",
                    "cache_write_usd_per_million": "1.25",
                    "output_usd_per_million": "2.0",
                },
            }
        }
    }
    (tmp_path / "models.yaml").write_text(yaml.safe_dump(models))
    path = tmp_path / "chat_models.yaml"
    path.write_text(yaml.safe_dump(content))
    return path


def test_catalog_exposes_public_facts_and_exact_decimal_costs() -> None:
    registry = load_chat_registry()
    model = registry.model(None)
    raw = yaml.safe_load((CONFIG_DIR / "chat_models.yaml").read_text())
    assert model.id == registry.default_model == raw["default_model"]
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


def test_a_small_catalogue_takes_its_facts_from_models_yaml(tmp_path: Path) -> None:
    model = load_chat_registry(_catalog(tmp_path)).model(None)
    assert (model.provider, model.display_name, model.context_window) == (
        "anthropic",
        "Model A",
        100,
    )
    assert model.cost(input_tokens=1_000_000, output_tokens=1_000_000) == 3


def _models(change: Any) -> Any:
    """A change to the models.yaml beside the catalogue."""

    def apply(_: dict[str, Any], models: dict[str, Any]) -> None:
        change(models["models"]["model-a"])

    return apply


def _chat(change: Any) -> Any:
    """A change to the chat catalogue."""

    def apply(chat: dict[str, Any], _: dict[str, Any]) -> None:
        change(chat)

    return apply


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (_chat(lambda data: data.update(version=2)), "version"),
        (_chat(lambda data: data.update(unexpected="value")), "unsupported fields"),
        (_chat(lambda data: data.update(default_model="missing")), "default_model"),
        (_models(lambda m: m.update(provider="openai")), "provider"),
        (_models(lambda m: m["pricing"].update(input_usd_per_million="NaN")), "priced"),
        (_models(lambda m: m.pop("pricing")), "not a priced model"),
        (_chat(lambda data: data["models"][0].update(input_usd_per_million="1")), "unsupported"),
        (_chat(lambda data: data["models"][0].update(max_output_tokens=101)), "context window"),
        (
            _chat(lambda data: data["models"][0].update(capabilities={"streaming": "yes"})),
            "capabilities",
        ),
        (_chat(lambda data: data["limits"].update(max_turns=True)), "positive integer"),
        (_chat(lambda data: data["providers"]["anthropic"].update(enabled=False)), "enabled"),
    ],
)
def test_invalid_registry_is_rejected(tmp_path: Path, change: Any, message: str) -> None:
    path = _catalog(tmp_path)
    chat = yaml.safe_load(path.read_text())
    models = yaml.safe_load((tmp_path / "models.yaml").read_text())
    change(chat, models)
    path.write_text(yaml.safe_dump(chat))
    (tmp_path / "models.yaml").write_text(yaml.safe_dump(models))
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
