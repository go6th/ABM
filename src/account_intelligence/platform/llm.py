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
* Token review 2026-10-20 (``platform.ports.CallOptions``): a research call may carry its
  reply's JSON Schema (sent as ``output_config.format``, built with the SDK's
  ``transform_schema``, so a reply cannot fail the schema and be asked again), a source document
  that opens the user turn as its own cached block, and a ``BatchSlot`` that sends it through
  the Message Batches API at half price. A batched call's system block is cached for one hour
  (``ttl: 1h``): batches finish minutes apart, past the five-minute default.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.platform.ports import (
    BatchSlot,
    CallOptions,
    LlmResult,
    LlmUsage,
    NotConfigured,
)
from obs.logging import logger

log = logger("platform.llm")

TIER_TASK = {
    "planner": "pull_planning",
    "extractor": "extraction",
    "classifier": "classification",
    "synthesis": "synthesis",
    "narrative": "narrative",
    "judge": "verification",
}


EFFORT_LEVELS = frozenset({"low", "medium", "high", "xhigh", "max"})
CACHE_TTLS = frozenset({"5m", "1h"})


@dataclass(frozen=True)
class ModelRoute:
    """Where one tier goes, and the request knobs of its task (``task_settings``)."""

    tier: str
    provider: str
    model: str
    effort: str | None = None
    prompt_cache: bool = False
    # the system block's cache lifetime ("5m" default, "1h" pays a 2x write to survive longer
    # pauses); a batched call always uses "1h"
    cache_ttl: str = "5m"


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
        ttl = str(knobs.get("cache_ttl", "5m"))
        if ttl not in CACHE_TTLS:
            raise ValueError(f"models.yaml: {task} has unknown cache_ttl {ttl!r}")
        routes[tier] = ModelRoute(
            tier,
            str(profile["provider"]),
            model,
            str(effort) if effort is not None else None,
            bool(knobs.get("prompt_cache", False)),
            ttl,
        )
    return routes


@lru_cache(maxsize=2)
def model_efforts(path: Path | None = None) -> dict[str, str]:
    """The fixed effort of a model (``effort`` on its ``models`` entry in models.yaml): every
    call on that model runs at it, whatever its task or surface asks. None is set: since
    2026-10-20 the user picks the effort beside each model (owner decision), and a model
    without one keeps the user's, task's or surface's effort."""
    data: dict[str, Any] = yaml.safe_load((path or CONFIG_DIR / "models.yaml").read_text())
    out = {}
    for model, spec in (data.get("models") or {}).items():
        effort = (spec or {}).get("effort")
        if effort is None:
            continue
        if str(effort) not in EFFORT_LEVELS:
            raise ValueError(f"models.yaml: {model} has unknown effort {effort!r}")
        out[str(model)] = str(effort)
    return out


def effort_for(model: str, asked: str | None) -> str | None:
    """The effort one call on ``model`` runs at: the model's fixed effort, else ``asked``."""
    return model_efforts().get(model, asked)


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


# schemas the API refused as structured output (by digest, per process): their calls go without
# the format from then on, the schema still in the prompt and the reply still checked in code
_REFUSED_SCHEMAS: set[str] = set()


class AnthropicLlm:
    """``LlmPort`` over an ``AsyncAnthropic`` or ``AsyncAnthropicVertex`` client (same API)."""

    supports_call_options = True

    def __init__(self, client: Any, routes: Mapping[str, ModelRoute], provider: str) -> None:
        self.client = client
        self.routes = dict(routes)
        self.provider = provider

    async def complete(
        self,
        tier: str,
        system: str,
        user: str,
        *,
        max_tokens: int,
        options: CallOptions | None = None,
    ) -> LlmResult:
        """One messages call for the tier's configured model; returns text and usage. With a
        ``BatchSlot`` (and a client that has the Batches API) the call goes through a message
        batch; a batch that errors, expires or is cancelled falls back to one standard call."""
        route = self.routes.get(tier)
        if route is None or route.provider != "anthropic":
            raise NotConfigured(f"tier {tier} has no anthropic route")
        options = options or CallOptions()
        schema_key = _schema_key(options.schema)
        if schema_key in _REFUSED_SCHEMAS:
            options = CallOptions(options.cached_user, None, options.batch, options.effort)
        if options.batch is not None and _has_batches(self.client):
            batched = await self._batched(route, system, user, max_tokens, options)
            if batched is not None:
                return batched
        return await self._standard(route, system, user, max_tokens, options)

    async def _standard(
        self, route: ModelRoute, system: str, user: str, max_tokens: int, options: CallOptions
    ) -> LlmResult:
        """One ``messages.create``; a structured-output schema the API refuses (400 naming the
        schema or format) is remembered and the call is made once more without it."""
        kwargs = request_kwargs(route, system, user, max_tokens, options)
        try:
            message = await self.client.messages.create(**kwargs)
        except Exception as error:  # noqa: BLE001 - narrowed below, everything else re-raised
            if options.schema is None or not _schema_refused(error):
                raise
            _REFUSED_SCHEMAS.add(_schema_key(options.schema))
            log.warning("llm.schema_refused", extra={"model": route.model, "tier": route.tier})
            plain = CallOptions(options.cached_user, None, None, options.effort)
            message = await self.client.messages.create(
                **request_kwargs(route, system, user, max_tokens, plain)
            )
        return self._result(message, batch=False)

    async def _batched(
        self, route: ModelRoute, system: str, user: str, max_tokens: int, options: CallOptions
    ) -> LlmResult | None:
        """The call through the Message Batches API, re-attaching to a batch an earlier slice
        submitted (``BatchSlot``). None when the batch did not succeed (errored, expired or
        cancelled after ``max_wait_seconds``): the caller then makes the standard call, which
        is the only payment for this request."""
        import asyncio

        slot: BatchSlot = options.batch  # type: ignore[assignment]
        record = await slot.load()
        if not record or not record.get("batch_id"):
            params = request_kwargs(route, system, user, max_tokens, options, ttl="1h")
            batch = await self.client.messages.batches.create(
                requests=[{"custom_id": "call", "params": params}]
            )
            record = {"batch_id": str(batch.id), "submitted_at": float(slot.now())}
            await slot.save(record)
            log.info("llm.batch_submitted", extra={"model": route.model, "tier": route.tier})
        batch_id = str(record["batch_id"])
        submitted = float(record.get("submitted_at") or slot.now())
        cancelled = False
        beat = float(slot.now())
        # LOOP(batch poll): cap=max_wait_seconds from the first submission, then cancel and at
        # most the same wait again for the cancel to land; exit=ended, cancel deadline
        while True:
            batch = await self.client.messages.batches.retrieve(batch_id)
            if str(batch.processing_status) == "ended":
                break
            waited = float(slot.now()) - submitted
            if not cancelled and waited > slot.max_wait_seconds:
                await self.client.messages.batches.cancel(batch_id)
                cancelled = True
                log.warning(
                    "llm.batch_cancelled",
                    extra={"model": route.model, "tier": route.tier, "waited_s": round(waited)},
                )
            elif cancelled and waited > 2 * slot.max_wait_seconds:
                return None
            if slot.heartbeat is not None and float(slot.now()) - beat >= slot.heartbeat_seconds:
                beat = float(slot.now())
                await slot.heartbeat()
            await asyncio.sleep(slot.poll_seconds)
        results = await self.client.messages.batches.results(batch_id)
        async for item in results:
            outcome = item.result
            if str(outcome.type) == "succeeded":
                return self._result(outcome.message, batch=True)
            log.warning(
                "llm.batch_unsuccessful",
                extra={"model": route.model, "tier": route.tier, "result": str(outcome.type)},
            )
        await slot.save({})  # the next attempt submits afresh
        return None

    def _result(self, message: Any, *, batch: bool) -> LlmResult:
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
                cache_creation_1h_input_tokens=getattr(
                    getattr(usage, "cache_creation", None), "ephemeral_1h_input_tokens", None
                ),
            ),
            extra={"provider": self.provider, "stop_reason": message.stop_reason, "batch": batch},
        )


def request_kwargs(
    route: ModelRoute,
    system: str,
    user: str,
    max_tokens: int,
    options: CallOptions | None = None,
    *,
    ttl: str | None = None,
) -> dict[str, Any]:
    """The ``messages.create`` arguments for one call: the task's effort and, when configured, the
    system prompt as one cached block (the only static prefix; run data stays in the user turn).
    A ``cached_user`` document opens the user turn as a second cached block, ahead of the
    question; a ``schema`` becomes structured output."""
    options = options or CallOptions()
    cache: dict[str, Any] = {"type": "ephemeral"}
    lifetime = ttl or route.cache_ttl
    if lifetime != "5m":
        cache["ttl"] = lifetime
    content: Any = user
    if options.cached_user:
        content = [
            {"type": "text", "text": options.cached_user, "cache_control": dict(cache)},
            {"type": "text", "text": user},
        ]
    kwargs: dict[str, Any] = {
        "model": route.model,
        "max_tokens": max_tokens,
        "system": (
            [{"type": "text", "text": system, "cache_control": dict(cache)}]
            if route.prompt_cache
            else system
        ),
        "messages": [{"role": "user", "content": content}],
    }
    output: dict[str, Any] = {}
    effort = options.effort or effort_for(route.model, route.effort)
    if effort is not None:
        output["effort"] = effort
    if options.schema is not None:
        output["format"] = {"type": "json_schema", "schema": structured_schema(options.schema)}
    if output:
        kwargs["output_config"] = output
    return kwargs


def structured_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """A reply schema in the form structured output accepts (the SDK's ``transform_schema``:
    closed objects, unsupported keywords moved into descriptions)."""
    import anthropic

    return dict(anthropic.transform_schema(dict(schema)))


def _schema_key(schema: Mapping[str, Any] | None) -> str:
    if schema is None:
        return ""
    import hashlib
    import json

    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


def _schema_refused(error: Exception) -> bool:
    """A 400 about the output format or its schema (too complex, unsupported keyword)."""
    status = getattr(error, "status_code", None)
    message = str(getattr(error, "message", "") or error).lower()
    return status == 400 and any(word in message for word in ("schema", "output_config", "format"))


def _has_batches(client: Any) -> bool:
    """Whether the client has the Message Batches API (the first-party API has; Vertex not)."""
    batches = getattr(getattr(client, "messages", None), "batches", None)
    return batches is not None and hasattr(batches, "create")


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

    supports_call_options = True

    async def complete(
        self,
        tier: str,
        system: str,
        user: str,
        *,
        max_tokens: int,
        options: CallOptions | None = None,
    ) -> LlmResult:
        """The tier's configured route (the default models)."""
        if self.anthropic is None:
            raise NotConfigured("no Anthropic model access is configured")
        return await self.anthropic.complete(
            tier, system, user, max_tokens=max_tokens, options=options
        )

    async def complete_as(
        self,
        model: str,
        tier: str,
        system: str,
        user: str,
        *,
        max_tokens: int,
        options: CallOptions | None = None,
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
            # Kimi has no batches, structured output or cache blocks: the document rides in
            # the user text
            if options is not None and options.cached_user:
                user = f"{options.cached_user}\n\n{user}"
            effort = options.effort if options is not None and options.effort else route.effort
            result: LlmResult = await self.moonshot.complete(
                model, system, user, max_tokens=max_tokens, effort=effort
            )
            return result
        if self.anthropic is None:
            raise NotConfigured("no Anthropic model access is configured")
        chosen = AnthropicLlm(
            self.anthropic.client, {tier: replace(route, model=model)}, self.anthropic.provider
        )
        return await chosen.complete(tier, system, user, max_tokens=max_tokens, options=options)


@dataclass(frozen=True)
class BatchConfig:
    """Which research calls go through the Message Batches API (``batch`` of models.yaml)."""

    enabled: bool = False
    task_kinds: frozenset[str] = frozenset()
    poll_seconds: float = 20.0
    max_wait_seconds: float = 1800.0

    def applies(self, task_kind: str) -> bool:
        """Whether one task kind's calls are batched."""
        return self.enabled and task_kind in self.task_kinds


@lru_cache(maxsize=2)
def load_batch_config(path: Path | None = None) -> BatchConfig:
    """The ``batch`` block of ``config/models.yaml``; absent means no call is batched."""
    data: dict[str, Any] = yaml.safe_load((path or CONFIG_DIR / "models.yaml").read_text())
    raw = data.get("batch") or {}
    cfg = BatchConfig(
        bool(raw.get("enabled", False)),
        frozenset(str(k) for k in raw.get("task_kinds") or ()),
        float(raw.get("poll_seconds", 20)),
        float(raw.get("max_wait_seconds", 1800)),
    )
    if cfg.poll_seconds <= 0 or cfg.max_wait_seconds <= cfg.poll_seconds:
        raise ValueError("models.yaml batch: poll_seconds must be positive and below max_wait")
    return cfg


@dataclass(frozen=True)
class HelperTarget:
    """A small helper call's model and token limit (``helpers`` of models.yaml)."""

    name: str
    model: str
    max_tokens: int


@lru_cache(maxsize=4)
def load_helper(name: str, path: Path | None = None) -> HelperTarget | None:
    """One helper's model from ``config/models.yaml`` ``helpers`` (None when it is not
    configured); the model must be on the allowlist and priced in ``models``."""
    data: dict[str, Any] = yaml.safe_load((path or CONFIG_DIR / "models.yaml").read_text())
    raw = (data.get("helpers") or {}).get(name)
    if not raw:
        return None
    model = str(raw["model"])
    if model not in {str(m) for m in data.get("allowlist", [])}:
        raise ValueError(f"models.yaml helpers: {name} names {model!r}, not on the allowlist")
    if model not in (data.get("models") or {}):
        raise ValueError(f"models.yaml helpers: {name} names {model!r}, which has no price")
    return HelperTarget(name, model, int(raw.get("max_tokens", 64)))
