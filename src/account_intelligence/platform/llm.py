"""``LlmPort`` adapters (decision D-19, LLM access path): the Anthropic API and Claude on Vertex AI.

Model ids live only in ``config/models.yaml``; a workflow tier (planner, extractor, classifier,
judge, synthesis, narrative) maps to a configured task, the task to a profile, the profile to a
provider and model. Verified against the installed ``anthropic`` package (1.11.0, see
``docs/open-items.md``):

* ``AsyncAnthropic`` and ``AsyncAnthropicVertex`` share ``messages.create(model, max_tokens,
  system, messages, ...)``; the response has ``content`` blocks and ``usage`` with
  ``input_tokens``, ``output_tokens``, ``cache_read_input_tokens``, ``cache_creation_input_tokens``.
* ``messages.create`` in this version has NO ``temperature`` parameter, so the spec's fixed
  temperature cannot be set here (open item OI-LLM-1). Determinism comes from schema validation
  and the bounded re-ask, never from sampling settings.
* The SDK client retries twice by default; the container builds clients with
  ``max_retries=0`` so the graph node ``RetryPolicy`` stays the single retry owner (W6).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.platform.ports import LlmResult, LlmUsage, NotConfigured

TIER_TASK = {
    "planner": "pull_planning",
    "extractor": "extraction",
    "classifier": "classification",
    "synthesis": "synthesis",
    "narrative": "narrative",
    "judge": "verification",
}


@dataclass(frozen=True)
class ModelRoute:
    """Where one tier goes."""

    tier: str
    provider: str
    model: str


@lru_cache(maxsize=2)
def load_routes(path: Path | None = None) -> dict[str, ModelRoute]:
    """Resolve every tier through the active arm of ``config/models.yaml``."""
    data: dict[str, Any] = yaml.safe_load((path or CONFIG_DIR / "models.yaml").read_text())
    arm = data["arms"][data["active_arm"]]["routes"]
    routes = {}
    for tier, task in TIER_TASK.items():
        profile = data["profiles"][arm[task]]
        routes[tier] = ModelRoute(tier, str(profile["provider"]), str(profile["model"]))
    return routes


def judge_differs_from_generator(routes: Mapping[str, ModelRoute]) -> bool:
    """G12 and workflow spec 2.3: the judge must not be the generator's model."""
    return routes["judge"].model != routes["extractor"].model


class AnthropicLlm:
    """``LlmPort`` over an ``AsyncAnthropic`` or ``AsyncAnthropicVertex`` client (same API)."""

    def __init__(self, client: Any, routes: Mapping[str, ModelRoute], provider: str) -> None:
        self.client = client
        self.routes = dict(routes)
        self.provider = provider

    async def complete(self, tier: str, system: str, user: str, *, max_tokens: int) -> LlmResult:
        """One messages call for the tier's configured model; returns text and usage."""
        route = self.routes.get(tier)
        if route is None or route.provider != "anthropic":
            raise NotConfigured(f"tier {tier} has no anthropic route")
        message = await self.client.messages.create(
            model=route.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(
            str(getattr(block, "text", "")) for block in message.content if block.type == "text"
        )
        usage = message.usage
        return LlmResult(
            text=text,
            model=str(message.model),
            usage=LlmUsage(
                input_tokens=int(usage.input_tokens),
                output_tokens=int(usage.output_tokens),
                cache_read_input_tokens=usage.cache_read_input_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens,
            ),
            extra={"provider": self.provider, "stop_reason": message.stop_reason},
        )


def anthropic_api_client(api_key: str) -> Any:
    """The Anthropic API client with SDK retries off (W6)."""
    import anthropic

    return anthropic.AsyncAnthropic(api_key=api_key, max_retries=0)


def vertex_client(project_id: str, region: str) -> Any:
    """Claude on Vertex AI with SDK retries off (W6). Uses the runtime service account's
    credentials (Application Default Credentials). VERIFY(V-LLM-2): the Vertex model id format
    and the models enabled in ``region`` are not verified here; they need the project."""
    import anthropic

    return anthropic.AsyncAnthropicVertex(project_id=project_id, region=region, max_retries=0)
