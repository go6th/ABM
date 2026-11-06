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
from obs.logging import logger

_log = logger(__name__)

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


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
        model: str | None = None,
        schema: Mapping[str, Any] | None = None,
        cached_user: str | None = None,
        max_tokens: int | None = None,
        effort: str | None = None,
        data_class: str | None = None,
    ) -> LlmResult:
        """One model call named by its task kind (R2): ``resolve_model`` routes it (``llm.route``)
        and the live port serves it. ``schema`` goes out as structured output, ``cached_user`` (a
        source document) opens the user turn as its own cached block, ``max_tokens`` raises the
        task kind's limit for a re-ask after a reply was cut off (token review 2026-10-20), and
        ``effort`` is an agent's own effort from the registry (``config/agents.yaml``).
        ``data_class`` is what the request carries (None: the run's, ``data_class``)."""
        return await self.resolve_model(
            task_kind,
            system,
            user,
            model,
            schema=schema,
            cached_user=cached_user,
            max_tokens=max_tokens,
            effort=effort,
            data_class=data_class,
        )

    @property
    def data_class(self) -> str:
        """The data class of the run's model requests (``llm.data_class``): ``tenant`` for a run
        whose input is the client's uploaded files, ``public_web`` otherwise."""
        return "tenant" if self.run_input.get("input_mode") == "file" else "public_web"

    def _guard_data_class(
        self, task_kind: str, tier: str, model: str | None, data_class: str
    ) -> None:
        """Rule R8 (ADR-0133): off, warn (logged, the call goes ahead) or enforce
        (``DataClassRefused`` before any reuse lookup or paid call)."""
        from llm.data_class import DataClassRefused, check, load_policy

        policy = load_policy()
        if policy.mode == "off":
            return
        effective = model or str(getattr(getattr(self.llm, "routes", {}).get(tier), "model", tier))
        verdict = check(policy, effective, data_class)
        if verdict == "ok":
            return
        _log.warning(
            "model.data_class_violation",
            extra={
                "task_kind": task_kind,
                "model": effective,
                "reason": data_class,
                "outcome": "refused" if verdict == "refuse" else "warned",
            },
        )
        if verdict == "refuse":
            raise DataClassRefused(effective, data_class)

    def chosen_model(self, tier: str, override: str | None = None) -> str | None:
        """The model a tier runs on: ``override`` (a fallback), else the run's choice for the
        tier; None when the run has no choice or the port cannot take one (the tier's route).

        A role left on its catalogue default is served by the active arm's route for each of its
        tiers (token review item 9, 2026-10-20: the cost_saving arm puts extraction on Sonnet
        5.5 and the judge on Opus 5.5 while planning stays on Opus 5.5); a model the user picked
        serves every tier of its role. The judge never runs on the extractor's model (G12): a
        judge that would (chosen or routed) runs on another Claude model of the allow-list, and
        ``JudgeNotIndependent`` is raised when there is none or the port cannot serve it (never
        a silent cap, ADR-0118; ``ensure_independent_judge`` checks it at run start)."""
        if override is not None:
            return override
        from llm.choice import catalogue, independent_judge
        from llm.route import JudgeNotIndependent

        cat = catalogue()
        routes = getattr(self.llm, "routes", {})
        can_choose = hasattr(self.llm, "complete_as")
        chosen: str | None = None
        if self.models is not None and can_choose:
            research = tier in cat.research_tiers
            picked = self.models.research if research else self.models.analysis
            default = cat.default_research if research else cat.default_analysis
            if picked != default or tier not in routes:
                chosen = str(self.models.for_tier(tier, cat))
        if tier != "judge" or "extractor" not in routes or "judge" not in routes:
            return chosen
        extractor = self.chosen_model("extractor") or str(routes["extractor"].model)
        judge = chosen or str(routes["judge"].model)
        if judge != extractor:
            return chosen
        other = independent_judge(extractor, cat)
        if not can_choose:
            raise JudgeNotIndependent(
                "the model port cannot serve a judge other than the extractor"
            )
        return other

    def ensure_independent_judge(self) -> None:
        """G12 at run start, before any paid call: the judge this run would use differs from its
        extractor, or ``JudgeNotIndependent`` (the runner ends the run FAILED with a full
        refund). A port without configured routes (the test fakes) has nothing to compare."""
        if "judge" in getattr(self.llm, "routes", {}):
            self.chosen_model("judge")

    def _effort(self, tier: str, agent_effort: str | None = None) -> str | None:
        """The effort of one call: an agent's own effort (``config/agents.yaml``), else the
        effort the run's user chose for the tier's role where the user's effort applies (not
        extraction or classification, ``llm.route.TIERS``); None: the task's configured one."""
        if agent_effort is not None:
            return agent_effort
        return self.models.effort_for_tier(tier) if self.models is not None else None

    def _cache_tier(self, tier: str, model: str | None) -> str:
        """The tier as the reply keys name it: the bare tier for the tier's configured model (so
        replies stored before the model choice existed are still reused, never paid again), the
        tier and model for any other model (a choice gets that model's answers)."""
        route = getattr(self.llm, "routes", {}).get(tier)
        if model is None or model == getattr(route, "model", None):
            return tier
        return f"{tier}@{model}"

    async def resolve_model(
        self,
        task_kind: str,
        system: str,
        user: str,
        override: str | None = None,
        *,
        schema: Mapping[str, Any] | None = None,
        cached_user: str | None = None,
        max_tokens: int | None = None,
        effort: str | None = None,
        data_class: str | None = None,
    ) -> LlmResult:
        """The one way into the model port (``platform.llm.ChoiceLlm``): the task kind's route
        (``llm.route``: tier, token limit), the run's model for the tier (``chosen_model``) and
        its effort (``_effort``), with two layers of reuse and a log of every call:

        1. within the run (R-replay): an answer is stored under the hash of its request in the
           run's payloads, so a node that a slice timeout cut off and the next slice re-runs gets
           the same answers back instead of paying for (and waiting on) them again;
        2. across runs of the same tenant (research cache, ``llm.reply_cache``): a request that is
           byte-identical after the run id is normalised out gets the stored reply text back, with
           this run's id substituted, while the reply is younger than its TTL.

        A reused answer reports zero usage. A task kind the routing table marks not cacheable
        is always asked afresh. Every call is logged to the reply log with how it was served, its
        tokens and its priced cost (never the prompt).

        A task kind the ``batch`` block of models.yaml lists goes through the Message Batches API
        at half price; its batch id is kept under the request digest, so a slice that ends while
        the batch runs leaves it running and the next slice collects it (never paid twice)."""
        import hashlib
        import json

        from llm.reply_cache import denormalise, normalise, reply_key
        from llm.route import route

        routed = route(task_kind)
        tier, replayable = str(routed.tier), routed.cacheable
        max_tokens = max_tokens or routed.max_tokens
        model = self.chosen_model(tier, override)
        # R8 before anything else: a refused data class never reaches a reply lookup or a model
        self._guard_data_class(task_kind, tier, model, data_class or self.data_class)
        key_tier = self._cache_tier(tier, model)
        effort = self._effort(tier, effort)
        if effort is not None:
            # a reply asked at another effort is another reply: trying high never reuses medium
            key_tier = f"{key_tier}~{effort}"
        # the request as the keys see it: a cached source block is part of the request
        keyed_user = f"{cached_user}\x00{user}" if cached_user else user
        if not replayable:
            await self._spend_check()
            await self._log_started(task_kind, tier, None, model)
            result = await self._complete(
                tier,
                system,
                user,
                max_tokens,
                model,
                schema=schema,
                cached_user=cached_user,
                effort=effort,
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
            task_kind,
            key_tier,
            max_tokens,
            system,
            normalise(keyed_user, self.run_id, self.account_id),
        )
        if self.reply_cache is not None and (self.reuse_cached or self.resume_from):
            hit = await self.reply_cache.get(
                self.tenant_id, shared_key, self.clock.now(), source_run=self.resume_from
            )
            stored_text = hit.text if hit is not None else ""
            if hit is None:
                # a reply logged before 2026-11-05 is keyed with the account id still in it: a
                # resume or rerun of the same account reuses it instead of paying once more
                legacy = reply_key(
                    task_kind, key_tier, max_tokens, system, normalise(keyed_user, self.run_id)
                )
                hit = await self.reply_cache.get(
                    self.tenant_id, legacy, self.clock.now(), source_run=self.resume_from
                )
                if hit is not None:
                    stored_text = normalise(
                        denormalise(hit.text, self.run_id), self.run_id, self.account_id
                    )
            if hit is not None:
                text = denormalise(stored_text, self.run_id, self.account_id)
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
                await self._log_reply(task_kind, tier, result, "cache", shared_key, stored_text)
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
            effort=effort,
        )
        if result.text.strip():  # an empty answer is not kept: a re-run asks again
            await self.payloads.put(
                key, json.dumps({"text": result.text, "model": result.model}).encode()
            )
            await self._log_reply(
                task_kind,
                tier,
                result,
                "model",
                shared_key,
                normalise(result.text, self.run_id, self.account_id),
            )
        else:
            await self._log_reply(task_kind, tier, result, "model", shared_key, None)
        return result

    async def _spend_check(self) -> None:
        """The run's hard dollar cap before a paid model call: provider and model spend
        together (``connectors.spend_guard``; ``DollarCapReached`` ends the run PARTIAL). Moves
        the heartbeat. The token guard comes first and applies with the caps off too
        (``TokenGuardReached``: the research graph pauses and asks the user)."""
        from account_intelligence.connectors import spend_guard

        if self.txn is None:  # a unit test's deps: no database, no ledger to measure
            return
        async with self.txn(self.tenant_id) as s:
            await spend_guard.check_tokens(s, self.run_id)
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
        effort: str | None = None,
    ) -> LlmResult:
        """The port call of ``resolve_model`` (its only caller), SDK errors typed
        (``llm.adapters.anthropic_api.map_error``): a 429, overload, 5xx, timeout or dropped
        connection is ``Overloaded`` (retryable), so the node RetryPolicy retries it (W6; the SDK
        client's own retries are off). A port without call options (the test fakes) gets the
        source document folded into the user text."""
        from llm.adapters.anthropic_api import map_error

        extra: dict[str, Any] = {}
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
