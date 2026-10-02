"""Environment-backed model tier routing with no provider model IDs in code."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from src.db.errors import DataLayerError

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "model_routing.yaml"
ROLES = {"planner", "extractor", "judge"}


@dataclass(frozen=True)
class ModelRoute:
    role: str
    tier: str
    provider: str | None
    model_id: str | None


def load_model_routes(
    config_path: Path = CONFIG_PATH, environment: dict[str, str] | None = None
) -> dict[str, ModelRoute]:
    try:
        config: Any = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DataLayerError(f"cannot load model routing config {config_path}: {exc}") from exc
    if not isinstance(config, dict):
        raise DataLayerError("model routing config must be a mapping")
    roles, tiers = config.get("roles"), config.get("tiers")
    if not isinstance(roles, dict) or set(roles) != ROLES or not isinstance(tiers, dict):
        raise DataLayerError("model routing must define planner, extractor and judge roles")
    env = os.environ if environment is None else environment
    routes: dict[str, ModelRoute] = {}
    for role, tier_name in roles.items():
        tier = tiers.get(tier_name)
        if not isinstance(tier_name, str) or not isinstance(tier, dict):
            raise DataLayerError(f"model route {role} references an unknown tier")
        provider_env, model_env = tier.get("provider_env"), tier.get("model_env")
        if not isinstance(provider_env, str) or not isinstance(model_env, str):
            raise DataLayerError(f"model tier {tier_name} requires provider_env and model_env")
        provider, model_id = env.get(provider_env), env.get(model_env)
        if (provider is None) != (model_id is None):
            raise DataLayerError(f"model tier {tier_name} needs both provider and model settings")
        routes[role] = ModelRoute(role, tier_name, provider, model_id)
    return routes
