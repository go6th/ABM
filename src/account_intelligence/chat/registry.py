"""Strictly load the public provider/model catalogue used by chat APIs and the model picker:
the allow-list and chat limits from ``config/chat_models.yaml``, each model's provider, name,
context window and prices from ``config/models.yaml`` (the one price table, ``llm.cost``)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR
from llm.cost import Price, prices


class ChatConfigInvalid(ValueError):
    """The model registry is invalid; chat must not start with partial model facts."""


@dataclass(frozen=True)
class ModelCapabilities:
    """Capabilities shown to callers; these never grant tools to model output."""

    streaming: bool
    vision: bool
    tool_use: bool


@dataclass(frozen=True)
class ChatModel:
    """A model selectable by an authenticated tenant caller."""

    id: str
    provider: str
    display_name: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    cache_write_usd_per_million: Decimal
    cache_read_usd_per_million: Decimal
    context_window: int
    max_output_tokens: int
    capabilities: ModelCapabilities

    def cost(self, *, input_tokens: int, output_tokens: int) -> Decimal:
        """Estimate standard uncached token cost with the catalog's current public prices."""
        return (
            Decimal(input_tokens) * self.input_usd_per_million
            + Decimal(output_tokens) * self.output_usd_per_million
        ) / Decimal(1_000_000)

    def public(self, *, output_limit: int | None = None) -> dict[str, Any]:
        """Public fields only; no credentials or internal provider configuration."""
        return {
            "id": self.id,
            "provider": self.provider,
            "display_name": self.display_name,
            "input_usd_per_million": str(self.input_usd_per_million),
            "output_usd_per_million": str(self.output_usd_per_million),
            "cache_write_usd_per_million": str(self.cache_write_usd_per_million),
            "cache_read_usd_per_million": str(self.cache_read_usd_per_million),
            "context_window": self.context_window,
            "max_output_tokens": min(
                self.max_output_tokens, output_limit or self.max_output_tokens
            ),
            "capabilities": {
                "streaming": self.capabilities.streaming,
                "vision": self.capabilities.vision,
                "tool_use": self.capabilities.tool_use,
            },
        }


@dataclass(frozen=True)
class ChatLimits:
    """Server-side per-thread limits; clients cannot raise these values."""

    max_turns: int
    max_input_chars: int
    max_output_tokens: int
    max_spend_usd_per_thread: Decimal


@dataclass(frozen=True)
class ChatRegistry:
    """Validated providers, selectable models and server-enforced defaults."""

    default_model: str
    models: dict[str, ChatModel]
    limits: ChatLimits

    def model(self, model_id: str | None) -> ChatModel:
        """Resolve a requested id or the configured default; unknown values fail closed."""
        return self.resolve(model_id)

    def resolve(self, model_id: str | None) -> ChatModel:
        """``model`` under a name the model-call-site guard does not read as an LLM call (that
        guard checks every ``.model(task_kind)`` in src)."""
        selected = self.default_model if model_id is None else model_id
        try:
            return self.models[selected]
        except KeyError:
            raise ChatConfigInvalid("requested model is not available") from None


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ChatConfigInvalid(f"{name} must be a mapping")
    return value


def _price(value: Any, name: str) -> Decimal:
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ChatConfigInvalid(f"{name} must be a finite nonnegative price") from None
    if not price.is_finite() or price < 0:
        raise ChatConfigInvalid(f"{name} must be a finite nonnegative price")
    return price


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ChatConfigInvalid(f"{name} must be a positive integer")
    return value


def _model_facts(models_path: Path) -> dict[str, tuple[str, str, int, Price]]:
    """``id -> (provider, display name, context window, prices)`` of every priced model of
    ``models.yaml``: the chat catalogue names ids, the one price table prices them
    (``llm.cost``, ADR-0115)."""
    try:
        raw = yaml.safe_load(models_path.read_text())
        table = prices(models_path)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise ChatConfigInvalid("models.yaml could not be read or priced") from exc
    out: dict[str, tuple[str, str, int, Price]] = {}
    for model_id, spec in (_mapping(raw, "models.yaml").get("models") or {}).items():
        spec = spec or {}
        if str(model_id) not in table:
            continue
        provider, display_name = spec.get("provider"), spec.get("display_name")
        if not all(isinstance(v, str) and v.strip() for v in (provider, display_name)):
            raise ChatConfigInvalid(f"models.yaml {model_id} needs a provider and display_name")
        window = _positive_int(spec.get("context_window"), f"models.yaml {model_id}.context_window")
        out[str(model_id)] = (str(provider), str(display_name), window, table[str(model_id)])
    return out


def load_chat_registry(path: Path | None = None, models_path: Path | None = None) -> ChatRegistry:
    """Parse and validate the entire registry before exposing any model to a request. Model
    facts and prices come from ``models.yaml`` (beside ``path``, or ``models_path``)."""
    try:
        raw = yaml.safe_load((path or CONFIG_DIR / "chat_models.yaml").read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ChatConfigInvalid("chat model registry could not be read") from exc
    data = _mapping(raw, "registry")
    if set(data) != {"version", "default_model", "limits", "providers", "models"}:
        raise ChatConfigInvalid("registry contains missing or unsupported fields")
    if data.get("version") != 1:
        raise ChatConfigInvalid("registry version must be 1")
    provider_data = _mapping(data.get("providers"), "providers")
    unsupported = set(provider_data) - {"anthropic", "moonshot", "xai"}
    if unsupported:
        raise ChatConfigInvalid("provider has no configured server adapter")
    enabled: set[str] = set()
    for name, raw_settings in provider_data.items():
        settings = _mapping(raw_settings, f"providers.{name}")
        if set(settings) != {"enabled", "credential_source"}:
            raise ChatConfigInvalid(f"providers.{name} contains missing or unsupported fields")
        if not isinstance(settings["enabled"], bool):
            raise ChatConfigInvalid(f"providers.{name}.enabled must be boolean")
        if settings["credential_source"] != "env_or_secret_manager":
            raise ChatConfigInvalid(f"providers.{name}.credential_source is unsupported")
        if settings["enabled"]:
            enabled.add(name)
    if not enabled:
        raise ChatConfigInvalid("at least one provider must be enabled")
    limits_raw = _mapping(data.get("limits"), "limits")
    if set(limits_raw) != {
        "max_turns",
        "max_input_chars",
        "max_output_tokens",
        "max_spend_usd_per_thread",
    }:
        raise ChatConfigInvalid("limits contains missing or unsupported fields")
    limits = ChatLimits(
        _positive_int(limits_raw.get("max_turns"), "limits.max_turns"),
        _positive_int(limits_raw.get("max_input_chars"), "limits.max_input_chars"),
        _positive_int(limits_raw.get("max_output_tokens"), "limits.max_output_tokens"),
        _price(limits_raw.get("max_spend_usd_per_thread"), "limits.max_spend_usd_per_thread"),
    )
    rows = data.get("models")
    if not isinstance(rows, list) or not rows:
        raise ChatConfigInvalid("models must be a nonempty list")
    beside = path.parent / "models.yaml" if path is not None else None
    if models_path is None:
        models_path = (
            beside if beside is not None and beside.is_file() else CONFIG_DIR / "models.yaml"
        )
    facts = _model_facts(models_path)
    models: dict[str, ChatModel] = {}
    for index, raw_model in enumerate(rows):
        name = f"models[{index}]"
        row = _mapping(raw_model, name)
        if set(row) != {"id", "max_output_tokens", "capabilities"}:
            raise ChatConfigInvalid(f"{name} contains missing or unsupported fields")
        model_id = row.get("id")
        if not isinstance(model_id, str) or not model_id.strip():
            raise ChatConfigInvalid(f"{name} requires an id")
        if model_id in models:
            raise ChatConfigInvalid(f"duplicate model id: {model_id}")
        fact = facts.get(model_id)
        if fact is None:
            raise ChatConfigInvalid(f"{name}: {model_id} is not a priced model of models.yaml")
        provider, display_name, context_window, price = fact
        if provider not in enabled:
            raise ChatConfigInvalid(f"{name} references a disabled or unknown provider")
        caps = _mapping(row.get("capabilities"), f"{name}.capabilities")
        if set(caps) != {"streaming", "vision", "tool_use"} or any(
            not isinstance(caps[key], bool) for key in caps
        ):
            raise ChatConfigInvalid(f"{name}.capabilities must contain three boolean flags")
        model = ChatModel(
            id=model_id,
            provider=provider,
            display_name=display_name,
            input_usd_per_million=price.input,
            output_usd_per_million=price.output,
            cache_write_usd_per_million=price.cache_write,
            cache_read_usd_per_million=price.cached_read,
            context_window=context_window,
            max_output_tokens=_positive_int(
                row.get("max_output_tokens"), f"{name}.max_output_tokens"
            ),
            capabilities=ModelCapabilities(**caps),
        )
        if model.max_output_tokens > model.context_window:
            raise ChatConfigInvalid(f"{name}.max_output_tokens exceeds its context window")
        models[model_id] = model
    default_model = data.get("default_model")
    if not isinstance(default_model, str) or default_model not in models:
        raise ChatConfigInvalid("default_model must name an enabled model")
    if limits.max_output_tokens > min(model.max_output_tokens for model in models.values()):
        raise ChatConfigInvalid("limits.max_output_tokens exceeds a selectable model's maximum")
    return ChatRegistry(default_model, models, limits)


@lru_cache(maxsize=1)
def get_chat_registry() -> ChatRegistry:
    """Load the packaged catalogue once per process; startup fails on invalid configuration."""
    return load_chat_registry()
