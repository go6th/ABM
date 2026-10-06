"""The router: the only entry to a model (D-48). ``Router.call`` does, in order:

1. resolve the task kind (``UnknownTaskKind`` when absent, R2) and its fixed knobs (R7);
2. compute the call key from the task kind, the prompt hash, the canonical input, the schema and
   the routing version; a stored call is **replayed** at no cost (R9);
3. select the candidate for this attempt from the ladder, the call's failed attempts and the
   shared health (``llm.routing.select``; fallback is selection, R4; data classes R8; judge
   independence R6);
4. build the deterministic cached prefix (R10) and check the token cap before the call (R9);
5. call the adapter; a typed failure is recorded as an attempt and health event and re-raised for
   the node RetryPolicy (the router holds no retry loop); a schema failure gets ONE re-ask, then
   ``SchemaInvalid`` (never a fallback, R3);
6. price the call (unknown price is ``None``, never zero), store the record (``degraded`` and
   its reason included, R5) and return the validated output.

No model output can choose a model, tier, prompt or budget (R11): every input to selection comes
from config, the call's own attempt history and health.

GUARANTEE(test_router_end_to_end_replays_records_and_degrades): R2 to R11 with a scripted model.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from llm import budget, cache, cost, schema
from llm.config import Candidate, RoutingConfig
from llm.errors import LlmError, Overloaded, SchemaInvalid
from llm.health import HealthStore
from llm.models import AdapterReply, Attempt, CallRecord, Routed, Scope
from llm.record import CallStore, call_key
from llm.routing import select

M = TypeVar("M", bound=BaseModel)


class ModelAdapter(Protocol):
    """One provider's messages call, errors mapped to ``llm.errors`` types."""

    async def complete(
        self,
        candidate: Candidate,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float | None,
        cache_prefix: bool,
    ) -> AdapterReply:
        """One call."""
        ...


class Router:
    """See the module docstring."""

    def __init__(
        self,
        cfg: RoutingConfig,
        adapters: Mapping[str, ModelAdapter],
        store: CallStore,
        health: HealthStore,
        now: Callable[[], datetime],
    ) -> None:
        self.cfg, self.adapters, self.store, self.health, self.now = (
            cfg,
            adapters,
            store,
            health,
            now,
        )

    async def call(
        self,
        task_kind: str,
        prompt_text: str,
        prompt_hash: str,
        payload: Mapping[str, Any],
        output: type[M],
        scope: Scope,
    ) -> Routed:
        """One routed call (see the module docstring)."""
        kind = self.cfg.task_kinds.get(task_kind)
        if kind is None:
            from llm.errors import UnknownTaskKind

            raise UnknownTaskKind(task_kind)
        user = cache.canonical(dict(payload))
        key = call_key(task_kind, prompt_hash, payload, output.__name__, self.cfg.routing_version)
        stored = await self.store.get(key)
        if stored is not None:
            return Routed(output.model_validate(stored.output), stored, True)
        history = await self.store.attempts(key)
        now = self.now()
        sel = select(
            self.cfg,
            task_kind,
            history,
            await self.health.open_circuits(now),
            scope.data_class,
            scope.generator_family,
        )
        prefix = cache.build(prompt_text)
        cap = self.cfg.token_caps.get(scope.depth)
        budget.check(
            await self.store.tokens_used(),
            budget.estimate_tokens(prefix, user, kind.max_tokens),
            cap,
        )
        adapter = self.adapters[sel.candidate.adapter]
        caching = kind.cacheable and sel.candidate.caching is True
        reply = await self._attempt(
            adapter, sel.candidate, key, prefix, user, kind.max_tokens, kind.temperature, caching
        )
        validated = schema.validate(reply.text, output)
        usage = reply.usage
        # LOOP(schema re-ask): cap=schema_reasks (1); exit=valid, SchemaInvalid
        for _ in range(self.cfg.schema_reasks):
            if validated is not None:
                break
            reply = await self._attempt(
                adapter,
                sel.candidate,
                key,
                prefix,
                user + schema.REASK_NOTE,
                kind.max_tokens,
                kind.temperature,
                caching,
            )
            usage = type(usage)(
                usage.input_tokens + reply.usage.input_tokens,
                usage.output_tokens + reply.usage.output_tokens,
                usage.cache_read_tokens + reply.usage.cache_read_tokens,
                usage.cache_write_tokens + reply.usage.cache_write_tokens,
            )
            validated = schema.validate(reply.text, output)
        if validated is None:
            await self.store.add_attempt(key, Attempt(sel.candidate.name, "SchemaInvalid"))
            raise SchemaInvalid(f"{task_kind}: output failed its schema after one re-ask")
        await self.health.record(sel.candidate.name, "success", self.now())
        record = CallRecord(
            call_key=key,
            task_kind=task_kind,
            tier=sel.tier,
            candidate=sel.candidate.name,
            model_alias=sel.candidate.name,
            provider_code=sel.candidate.provider_code,
            family=sel.candidate.family,
            routing_version=self.cfg.routing_version,
            prompt_hash=prompt_hash,
            degraded=sel.degraded,
            degraded_reason=sel.reason,
            usage=usage,
            cost_usd=cost.cost_usd(sel.candidate.price, usage),
            output=validated.model_dump(mode="json"),
        )
        stored = await self.store.put(record)
        return Routed(output.model_validate(stored.output), stored, False)

    async def _attempt(
        self,
        adapter: ModelAdapter,
        candidate: Candidate,
        key: str,
        prefix: str,
        user: str,
        max_tokens: int,
        temperature: float | None,
        caching: bool,
    ) -> AdapterReply:
        try:
            return await adapter.complete(
                candidate,
                prefix,
                user,
                max_tokens=max_tokens,
                temperature=temperature,
                cache_prefix=caching,
            )
        except (LlmError, Overloaded) as exc:
            await self.store.add_attempt(key, Attempt(candidate.name, type(exc).__name__))
            if isinstance(exc, Overloaded) or type(exc).__name__ == "ModelUnavailable":
                await self.health.record(candidate.name, "failure", self.now())
            raise
