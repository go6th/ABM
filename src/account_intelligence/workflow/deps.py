"""Everything one run's stages need, built by ``app.container`` and passed to the handlers.

Identity (tenant, run, account) is taken from the run row loaded under the verified tenant, never
from a request body, a tool argument or model output (W3). Stages receive only this object and
the small checkpoint state.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.connectors.base import CallContext
from account_intelligence.grading.config import load_grading_config
from account_intelligence.platform.ports import (
    BatchSlot,
    CallOptions,
    Clock,
    LlmPort,
    LlmResult,
    LlmUsage,
    PayloadMissing,
    PayloadStore,
)
from account_intelligence.tracker.adapters import LedgerCallAdapter, RunInputAdapter
from account_intelligence.tracker.repo import CoverageRepo
from account_intelligence.tracker.service import TrackerService
from account_intelligence.workflow.spec import WorkflowSpec

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


class _JsonObject(BaseModel):
    """A routed call's output is a JSON object; each caller validates it strictly against its
    own schema afterwards (the router checks the object shape, R3)."""

    model_config = ConfigDict(extra="allow")


@dataclass
class RunDeps:
    """One run's dependencies. ``connectors`` is ``None`` when no provider is configured: every
    research row then ends Not done with ``not_available_yet`` (never a fake success)."""

    tenant_id: UUID
    run_id: UUID
    account_id: UUID
    domain: str
    txn: TxnFactory
    llm: LlmPort
    payloads: PayloadStore
    clock: Clock
    spec: WorkflowSpec
    connectors: Any | None = None
    builder: Any | None = None
    gate: Any | None = None
    judge: Any | None = None
    canary: str = "CANARY-7f3a9e"
    retry_max_attempts: int = 3
    extra_allowed_hosts: frozenset[str] = field(default_factory=frozenset)
    run_input: dict[str, Any] = field(default_factory=dict)
    deadline: float | None = None
    # credit cap ports (spend.ports): free balance check and operator prompt; None in production
    # until a balance endpoint and a notifier exist (balances then read ``unknown``)
    balances: Any | None = None
    notifier: Any | None = None
    # local stack only (DEV_AUTO_APPROVE_CAPS=1): the cap is approved as tenant policy, as if the
    # tenant had set ``auto_approve_within_reserve``; ``spend.approval`` rules still hold
    dev_auto_approve: bool = False
    # model routing (D-48): with a router every call goes through it; None uses ``llm`` directly
    router: Any | None = None
    # the models this run uses (``llm.choice.RunModels``: the user's research and analysis
    # models, owner decision 2026-10-14); None serves every tier with its configured route
    models: Any | None = None
    # research cache: the model reply log and cross-run reply reuse (``llm.reply_cache``)
    reply_cache: Any | None = None

    @property
    def resume_from(self) -> UUID | None:
        """The run this one continues (``run_input {"resume_from": ...}``), validated at creation
        to be a run of this tenant and account; None for a normal run."""
        value = self.run_input.get("resume_from")
        try:
            return UUID(str(value)) if value else None
        except ValueError:
            return None

    @property
    def reuse_cached(self) -> bool:
        """Whether this run may reuse finished calls and replies of earlier runs of the tenant
        (research cache); ``run_input {"fresh": true}`` pays for everything anew."""
        return not bool(self.run_input.get("fresh"))

    def call_context(self) -> CallContext:
        """Connector call identity, from the run (never from arguments)."""
        return CallContext(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            account_id=self.account_id,
            account_domain=self.domain,
            extra_allowed_hosts=self.extra_allowed_hosts,
            deadline=self.deadline,
            reuse_cached=self.reuse_cached,
            resume_from=self.resume_from,
        )

    def tracker(self, session: AsyncSession) -> TrackerService:
        """The tracker on one tenant transaction, wired to the connector ledger."""
        return TrackerService(
            CoverageRepo(session),
            LedgerCallAdapter(session, load_grading_config().category_by_operation),
            RunInputAdapter(session),
            owners=self.spec.coverage_owners,
        )

    async def model(
        self,
        task_kind: str,
        system: str,
        user: str,
        *,
        data_class: str = "public_web",
        model: str | None = None,
        schema: Mapping[str, Any] | None = None,
        cached_user: str | None = None,
        max_tokens: int | None = None,
    ) -> LlmResult:
        """One model call named by its task kind (R2): the knobs come from config (R7); with a
        router the call is routed, recorded and replayable; otherwise the legacy port serves it.
        On the legacy port ``schema`` goes out as structured output, ``cached_user`` (a source
        document) opens the user turn as its own cached block, and ``max_tokens`` raises the task
        kind's limit for a re-ask after a reply was cut off (token review 2026-10-20)."""
        from llm.tasks import require

        kind = require(task_kind)
        if self.router is None:
            return await self._legacy_model(
                task_kind,
                system,
                user,
                max_tokens or kind.max_tokens,
                bool(kind.cacheable),
                model,
                schema=schema,
                cached_user=cached_user,
            )
        if cached_user:
            user = f"{cached_user}\n\n{user}"
        from llm.models import Scope
        from llm.prompts import prompt_hash

        await self._spend_check()
        routed = await self.router(self).call(
            task_kind,
            system,
            prompt_hash(system),
            {"user": user},
            _JsonObject,
            Scope(self.tenant_id, self.run_id, data_class),
        )
        import json

        usage = routed.record.usage
        return LlmResult(
            text=json.dumps(routed.output.model_dump(mode="json")),
            model=routed.record.model_alias,
            usage=LlmUsage(
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_read_tokens,
                usage.cache_write_tokens,
            ),
            extra={"degraded": routed.record.degraded, "replayed": routed.replayed},
        )

    def chosen_model(self, tier: str, override: str | None = None) -> str | None:
        """The model a tier runs on: ``override`` (a fallback), else the run's choice for the
        tier; None when the run has no choice or the port cannot take one (the tier's route).

        A role left on its catalogue default is served by the active arm's route for each of its
        tiers (token review item 9, 2026-10-20: the cost_saving arm puts extraction on Sonnet
        5.5 and the judge on Opus 5.5 while planning stays on Opus 5.5); a model the user picked
        serves every tier of its role. The judge never runs on the extractor's model (G12)."""
        if override is not None:
            return override
        if self.models is None or not hasattr(self.llm, "complete_as"):
            return None
        from llm.choice import catalogue, independent_judge

        cat = catalogue()
        routes = getattr(self.llm, "routes", {})
        research = tier in cat.research_tiers
        picked = self.models.research if research else self.models.analysis
        default = cat.default_research if research else cat.default_analysis
        if picked == default and tier in routes:
            chosen: str | None = None
        else:
            chosen = str(self.models.for_tier(tier, cat))
        if tier != "judge" or "extractor" not in routes:
            return chosen
        extractor = self.chosen_model("extractor") or str(routes["extractor"].model)
        judge = chosen or str(routes["judge"].model)
        return independent_judge(extractor, cat) if judge == extractor else chosen

    def _effort(self, tier: str) -> str | None:
        """The effort the run's user chose for this tier's role (None: the task's own)."""
        return self.models.effort_for_tier(tier) if self.models is not None else None

    def _cache_tier(self, tier: str, model: str | None) -> str:
        """The tier as the reply keys name it: the bare tier for the tier's configured model (so
        replies stored before the model choice existed are still reused, never paid again), the
        tier and model for any other model (a choice gets that model's answers)."""
        route = getattr(self.llm, "routes", {}).get(tier)
        if model is None or model == getattr(route, "model", None):
            return tier
        return f"{tier}@{model}"

    async def _legacy_model(
        self,
        task_kind: str,
        system: str,
        user: str,
        max_tokens: int,
        replayable: bool,
        override: str | None = None,
        *,
        schema: Mapping[str, Any] | None = None,
        cached_user: str | None = None,
    ) -> LlmResult:
        """The legacy port with two layers of reuse and a log of every call:

        1. within the run (R-replay): an answer is stored under the hash of its request in the
           run's payloads, so a node that a slice timeout cut off and the next slice re-runs gets
           the same answers back instead of paying for (and waiting on) them again;
        2. across runs of the same tenant (research cache, ``llm.reply_cache``): a request that is
           byte-identical after the run id is normalised out gets the stored reply text back, with
           this run's id substituted, while the reply is younger than its TTL.

        A reused answer reports zero usage. A task kind the routing config marks not cacheable
        is always asked afresh. Every call is logged to the reply log with how it was served, its
        tokens and its priced cost (never the prompt).

        A task kind the ``batch`` block of models.yaml lists goes through the Message Batches API
        at half price; its batch id is kept under the request digest, so a slice that ends while
        the batch runs leaves it running and the next slice collects it (never paid twice)."""
        import hashlib
        import json

        from llm.reply_cache import denormalise, normalise, reply_key
        from llm.tasks import LEGACY_TIER

        tier = LEGACY_TIER[task_kind]
        model = self.chosen_model(tier, override)
        key_tier = self._cache_tier(tier, model)
        effort = self._effort(tier)
        if effort is not None:
            # a reply asked at another effort is another reply: trying high never reuses medium
            key_tier = f"{key_tier}~{effort}"
        # the request as the keys see it: a cached source block is part of the request
        keyed_user = f"{cached_user}\x00{user}" if cached_user else user
        if not replayable:
            await self._spend_check()
            await self._log_started(task_kind, tier, None, model)
            result = await self._complete(
                tier, system, user, max_tokens, model, schema=schema, cached_user=cached_user
            )
            await self._log_reply(task_kind, tier, result, "model", None, None)
            return result
        digest = hashlib.sha256(
            json.dumps([task_kind, key_tier, max_tokens, system, keyed_user]).encode()
        ).hexdigest()
        key = self.payload_key("model_replies", f"{digest}.json")
        try:
            stored = json.loads(await self.payloads.get(key))
            return LlmResult(stored["text"], stored["model"], LlmUsage(0, 0))
        except PayloadMissing:
            pass
        shared_key = reply_key(
            task_kind, key_tier, max_tokens, system, normalise(keyed_user, self.run_id)
        )
        if self.reply_cache is not None and (self.reuse_cached or self.resume_from):
            hit = await self.reply_cache.get(
                self.tenant_id, shared_key, self.clock.now(), source_run=self.resume_from
            )
            if hit is not None:
                text = denormalise(hit.text, self.run_id)
                await self.payloads.put(
                    key, json.dumps({"text": text, "model": hit.model}).encode()
                )
                result = LlmResult(
                    text,
                    hit.model,
                    LlmUsage(0, 0),
                    extra={"cached": True, "saved_tokens": hit.input_tokens + hit.output_tokens},
                )
                # keep the text on the reuse row too, so a later resume of THIS run reuses it
                await self._log_reply(task_kind, tier, result, "cache", shared_key, hit.text)
                return result
        await self._spend_check()
        await self._log_started(task_kind, tier, shared_key, model)
        result = await self._complete(
            tier,
            system,
            user,
            max_tokens,
            model,
            schema=schema,
            cached_user=cached_user,
            batch=self._batch_slot(task_kind, tier, model, digest),
        )
        if result.text.strip():  # an empty answer is not kept: a re-run asks again
            await self.payloads.put(
                key, json.dumps({"text": result.text, "model": result.model}).encode()
            )
            await self._log_reply(
                task_kind, tier, result, "model", shared_key, normalise(result.text, self.run_id)
            )
        else:
            await self._log_reply(task_kind, tier, result, "model", shared_key, None)
        return result

    async def _spend_check(self) -> None:
        """The run's hard dollar cap before a paid model call: provider and model spend
        together (``connectors.spend_guard``; ``DollarCapReached`` ends the run PARTIAL). Moves
        the heartbeat."""
        from account_intelligence.connectors import spend_guard

        if self.txn is None:  # a unit test's deps: no database, no ledger to measure
            return
        async with self.txn(self.tenant_id) as s:
            await spend_guard.check_cap(s, self.run_id)

    async def _log_started(
        self, task_kind: str, tier: str, shared_key: str | None, chosen: str | None = None
    ) -> None:
        """Write-ahead: one ``started`` row before the paid model request (no text, no tokens),
        so a call the process died in stays visible. Never a reuse hit; never fails the call."""
        if self.reply_cache is None:
            return
        from llm.reply_cache import ReplyUsage

        route = getattr(self.llm, "routes", {}).get(tier)
        model = chosen or str(getattr(route, "model", "") or tier)
        try:
            await self.reply_cache.record(
                self.tenant_id,
                self.run_id,
                shared_key or "",
                task_kind,
                tier,
                model,
                "started",
                ReplyUsage(),
                None,
                None,
            )
        except Exception:  # noqa: BLE001 - the log is an aid; the call is what matters
            return

    async def _log_reply(
        self,
        task_kind: str,
        tier: str,
        result: LlmResult,
        served_from: str,
        shared_key: str | None,
        reply_text: str | None,
    ) -> None:
        """One reply-log row (tokens, priced cost, how served); a logging failure never fails
        the call."""
        if self.reply_cache is None:
            return
        from llm.reply_cache import ReplyUsage, price

        usage = ReplyUsage(
            result.usage.input_tokens,
            result.usage.output_tokens,
            result.usage.cache_read_input_tokens or 0,
            result.usage.cache_creation_input_tokens or 0,
            result.usage.cache_creation_1h_input_tokens or 0,
            bool(result.extra.get("batch")) if served_from == "model" else False,
        )
        try:
            await self.reply_cache.record(
                self.tenant_id,
                self.run_id,
                shared_key or "",
                task_kind,
                tier,
                result.model,
                served_from,
                usage,
                price(result.model, usage) if served_from == "model" else None,
                reply_text,
            )
        except Exception:  # noqa: BLE001 - the log is an aid; the answer is what matters
            return

    def _batch_slot(
        self, task_kind: str, tier: str, model: str | None, digest: str
    ) -> BatchSlot | None:
        """The batch slot of one call when its task kind is batched (models.yaml ``batch``), its
        model is a Claude model and the port can batch; None otherwise."""
        import json
        import time

        from account_intelligence.platform.llm import load_batch_config

        cfg = load_batch_config()
        if not cfg.applies(task_kind) or not getattr(self.llm, "supports_call_options", False):
            return None
        if model is not None:
            from llm.choice import catalogue

            choice = catalogue().get(model)
            if choice is None or choice.provider != "anthropic":
                return None
        else:
            route = getattr(self.llm, "routes", {}).get(tier)
            if route is None or getattr(route, "provider", None) != "anthropic":
                return None
        key = self.payload_key("model_batches", f"{digest}.json")
        payloads = self.payloads

        async def load() -> dict[str, Any] | None:
            try:
                raw = json.loads(await payloads.get(key))
            except PayloadMissing:
                return None
            return raw if isinstance(raw, dict) else None

        async def save(record: dict[str, Any]) -> None:
            await payloads.put(key, json.dumps(record).encode())

        beat: Any = None
        if self.txn is not None:
            txn, tenant_id, run_id = self.txn, self.tenant_id, self.run_id

            async def touch() -> None:
                from account_intelligence.connectors import spend_guard

                async with txn(tenant_id) as s:
                    await spend_guard.touch(s, run_id)

            beat = touch
        return BatchSlot(load, save, time.time, cfg.poll_seconds, cfg.max_wait_seconds, beat)

    async def _complete(
        self,
        tier: str,
        system: str,
        user: str,
        max_tokens: int,
        model: str | None = None,
        *,
        schema: Mapping[str, Any] | None = None,
        cached_user: str | None = None,
        batch: BatchSlot | None = None,
    ) -> LlmResult:
        """One legacy call, its SDK errors typed like the router's adapter: a 429, overload, 5xx,
        timeout or dropped connection is ``Overloaded`` (retryable), so the node RetryPolicy
        retries it (W6; the SDK client's own retries are off). A port without call options
        (the test fakes) gets the source document folded into the user text."""
        from llm.adapters.anthropic_api import map_error

        extra: dict[str, Any] = {}
        effort = self._effort(tier)
        if getattr(self.llm, "supports_call_options", False):
            if schema is not None or cached_user or batch is not None or effort is not None:
                extra["options"] = CallOptions(cached_user or None, schema, batch, effort)
        elif cached_user:
            user = f"{cached_user}\n\n{user}"
        try:
            if model is not None:
                # the choice port (``platform.llm.ChoiceLlm``); ``chosen_model`` checked it exists
                complete_as = getattr(self.llm, "complete_as")  # noqa: B009 - not on LlmPort
                result: LlmResult = await complete_as(
                    model, tier, system, user, max_tokens=max_tokens, **extra
                )
                return result
            return await self.llm.complete(tier, system, user, max_tokens=max_tokens, **extra)
        except Exception as error:
            # map provider errors to the platform's types; anything unmapped is re-raised
            mapped = map_error(error)
            if mapped is error:
                raise
            raise mapped from error

    def payload_key(self, kind: str, name: str) -> str:
        """Neutral key under this run (no provider name, no account name)."""
        return (
            f"tenants/{self.tenant_id}/accounts/{self.account_id}/runs/{self.run_id}/{kind}/{name}"
        )
