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
* ``messages.create`` accepts ``output_config`` (``effort``) and a ``system`` list of text blocks
  with ``cache_control`` (verified 2026-10-13 against the installed SDK signature). The per-task
  ``task_settings`` of ``config/models.yaml`` decide both: effort bounds how much the model
  thinks (thinking tokens count against ``max_tokens`` on these models), and a cached system
  block makes repeated calls of one task pay the cached-read price for the prompt and schema.
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


EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})


@dataclass(frozen=True)
class ModelRoute:
    """Where one tier goes, and the request knobs of its task (``task_settings``)."""

    tier: str
    provider: str
    model: str
    effort: str | None = None
    prompt_cache: bool = False


@lru_cache(maxsize=2)
def load_routes(path: Path | None = None) -> dict[str, ModelRoute]:
    """Resolve every tier through the active arm of ``config/models.yaml``; the model of every
    route must be on the allowlist and an effort must be a known level."""
    data: dict[str, Any] = yaml.safe_load((path or CONFIG_DIR / "models.yaml").read_text())
    arm = data["arms"][data["active_arm"]]["routes"]
    allowed = {str(m) for m in data.get("allowlist", [])}
    settings: dict[str, Any] = data.get("task_settings") or {}
    routes = {}
    for tier, task in TIER_TASK.items():
        profile = data["profiles"][arm[task]]
        model = str(profile["model"])
        if model not in allowed:
            raise ValueError(f"models.yaml: {task} routes to {model!r}, not on the allowlist")
        knobs = settings.get(task) or {}
        effort = knobs.get("effort")
        if effort is not None and str(effort) not in EFFORT_LEVELS:
            raise ValueError(f"models.yaml: {task} has unknown effort {effort!r}")
        routes[tier] = ModelRoute(
            tier,
            str(profile["provider"]),
            model,
            str(effort) if effort is not None else None,
            bool(knobs.get("prompt_cache", False)),
        )
    return routes


def judge_differs_from_generator(routes: Mapping[str, ModelRoute]) -> bool:
    """G12 and workflow spec 2.3: the judge must not be the generator's model."""
    return routes["judge"].model != routes["extractor"].model


@dataclass(frozen=True)
class SurfaceTarget:
    """The model and effort one conversational surface runs on (``surfaces`` of models.yaml)."""

    surface: str
    model: str | None
    effort: str | None


@lru_cache(maxsize=2)
def load_surfaces(path: Path | None = None) -> dict[str, SurfaceTarget]:
    """The conversational surfaces' model/effort from ``config/models.yaml`` (owner decision
    2026-10-17: one layer every surface reads — research, Ask and the evals all run on Kimi K3,
    Claude Fable 5.1 or Claude Opus 5.5, and switching is config here, never code). The block is
    optional: its absence reproduces the behavior before it existed (the chat providers without
    an effort, the tabular eval on kimi-k3 at high). A surface model must be one of the choices
    models; an effort must be a known level."""
    from llm.choice import catalogue

    data: dict[str, Any] = yaml.safe_load((path or CONFIG_DIR / "models.yaml").read_text())
    ids = set(catalogue().ids)
    targets = {}
    for name, knobs in (data.get("surfaces") or {}).items():
        knobs = knobs or {}
        model, effort = knobs.get("model"), knobs.get("effort")
        if model is not None and str(model) not in ids:
            raise ValueError(f"models.yaml surfaces: {name} names {model!r}, not a choices model")
        if effort is not None and str(effort) not in EFFORT_LEVELS:
            raise ValueError(f"models.yaml surfaces: {name} has unknown effort {effort!r}")
        targets[str(name)] = SurfaceTarget(
            str(name),
            str(model) if model is not None else None,
            str(effort) if effort is not None else None,
        )
    return targets


def chat_effort() -> str | None:
    """The effort every Ask and follow-up-chat provider is built with (None = no effort set,
    the provider's own default stands)."""
    target = load_surfaces().get("chat")
    return target.effort if target is not None else None


def resolve_eval_choice(env: Mapping[str, str] | None = None) -> SurfaceTarget:
    """The live tabular eval's model and effort: ``EVAL_MODEL`` / ``EVAL_EFFORT`` override the
    ``surfaces.eval`` entry, whose default keeps the landed kimi-k3-at-high suite unchanged.
    Anthropic models run at temperature 0 without a seed (the SDK's Messages call has no
    temperature or seed parameter); the answer assertions stay the hard gate either way."""
    import os

    from llm.choice import catalogue

    env = os.environ if env is None else env
    target = load_surfaces().get("eval") or SurfaceTarget("eval", "kimi-k3", "high")
    model = env.get("EVAL_MODEL") or target.model
    effort = env.get("EVAL_EFFORT") or target.effort
    if model is None or model not in catalogue().ids:
        raise ValueError(f"the eval surface's model {model!r} is not a choices model")
    if effort is not None and effort not in EFFORT_LEVELS:
        raise ValueError(f"the eval surface has unknown effort {effort!r}")
    return SurfaceTarget("eval", model, effort)


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
            **request_kwargs(route, system, user, max_tokens)
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


def request_kwargs(route: ModelRoute, system: str, user: str, max_tokens: int) -> dict[str, Any]:
    """The ``messages.create`` arguments for one call: the task's effort and, when configured, the
    system prompt as one cached block (the only static prefix; run data stays in the user turn)."""
    kwargs: dict[str, Any] = {
        "model": route.model,
        "max_tokens": max_tokens,
        "system": (
            [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
            if route.prompt_cache
            else system
        ),
        "messages": [{"role": "user", "content": user}],
    }
    if route.effort is not None:
        kwargs["output_config"] = {"effort": route.effort}
    return kwargs


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


class ChoiceLlm:
    """The live model port with the user's model choice (owner decision 2026-10-14): ``complete``
    serves a tier with its configured route, as before; ``complete_as`` serves a tier with a
    chosen model, on the Anthropic port for Claude models and on the Moonshot client for Kimi K3
    (``llm.adapters.moonshot_api``), keeping the tier's effort and token limit. A provider that is
    not set up raises ``NotConfigured``; the API refuses such a choice before any work starts."""

    def __init__(self, anthropic: AnthropicLlm | None, moonshot: Any | None = None) -> None:
        self.anthropic = anthropic
        self.moonshot = moonshot
        self.routes = dict(anthropic.routes) if anthropic is not None else {}
        self.client = getattr(anthropic, "client", None)

    async def complete(self, tier: str, system: str, user: str, *, max_tokens: int) -> LlmResult:
        """The tier's configured route (the default models)."""
        if self.anthropic is None:
            raise NotConfigured("no Anthropic model access is configured")
        return await self.anthropic.complete(tier, system, user, max_tokens=max_tokens)

    async def complete_as(
        self, model: str, tier: str, system: str, user: str, *, max_tokens: int
    ) -> LlmResult:
        """One call of ``tier`` on ``model``."""
        from dataclasses import replace

        from llm.choice import catalogue

        choice = catalogue().get(model)
        route = self.routes.get(tier)
        if choice is None or route is None:
            raise NotConfigured(f"tier {tier} or model choice is not configured")
        if choice.provider == "moonshot":
            if self.moonshot is None:
                raise NotConfigured("Kimi K3 is not set up (MOONSHOT_API_KEY)")
            result: LlmResult = await self.moonshot.complete(
                model, system, user, max_tokens=max_tokens, effort=route.effort
            )
            return result
        if self.anthropic is None:
            raise NotConfigured("no Anthropic model access is configured")
        chosen = AnthropicLlm(
            self.anthropic.client, {tier: replace(route, model=model)}, self.anthropic.provider
        )
        return await chosen.complete(tier, system, user, max_tokens=max_tokens)
