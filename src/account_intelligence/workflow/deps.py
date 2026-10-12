"""Everything one run's stages need, built by ``app.container`` and passed to the handlers.

Identity (tenant, run, account) is taken from the run row loaded under the verified tenant, never
from a request body, a tool argument or model output (W3). Stages receive only this object and
the small checkpoint state.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from account_intelligence.connectors.base import CallContext
from account_intelligence.grading.config import load_grading_config
from account_intelligence.platform.ports import (
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

    def call_context(self) -> CallContext:
        """Connector call identity, from the run (never from arguments)."""
        return CallContext(
            tenant_id=self.tenant_id,
            run_id=self.run_id,
            account_id=self.account_id,
            account_domain=self.domain,
            extra_allowed_hosts=self.extra_allowed_hosts,
            deadline=self.deadline,
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
        self, task_kind: str, system: str, user: str, *, data_class: str = "public_web"
    ) -> LlmResult:
        """One model call named by its task kind (R2): the knobs come from config (R7); with a
        router the call is routed, recorded and replayable; otherwise the legacy port serves it."""
        from llm.tasks import require

        kind = require(task_kind)
        if self.router is None:
            return await self._legacy_model(
                task_kind, system, user, kind.max_tokens, bool(kind.cacheable)
            )
        from llm.models import Scope
        from llm.prompts import prompt_hash

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

    async def _legacy_model(
        self, task_kind: str, system: str, user: str, max_tokens: int, replayable: bool
    ) -> LlmResult:
        """The legacy port, replayable within the run like the router (R-replay): an answer is
        stored under the hash of its request, so a node that a slice timeout cut off and the next
        slice re-runs gets the same answers back instead of paying for (and waiting on) them
        again. A stored answer reports zero usage. A task kind the routing config marks not
        cacheable is always asked afresh."""
        import hashlib
        import json

        from llm.tasks import LEGACY_TIER

        tier = LEGACY_TIER[task_kind]
        if not replayable:
            return await self._complete(tier, system, user, max_tokens)
        digest = hashlib.sha256(
            json.dumps([task_kind, tier, max_tokens, system, user]).encode()
        ).hexdigest()
        key = self.payload_key("model_replies", f"{digest}.json")
        try:
            stored = json.loads(await self.payloads.get(key))
            return LlmResult(stored["text"], stored["model"], LlmUsage(0, 0))
        except PayloadMissing:
            pass
        result = await self._complete(tier, system, user, max_tokens)
        if result.text.strip():  # an empty answer is not kept: a re-run asks again
            await self.payloads.put(
                key, json.dumps({"text": result.text, "model": result.model}).encode()
            )
        return result

    async def _complete(self, tier: str, system: str, user: str, max_tokens: int) -> LlmResult:
        """One legacy call, its SDK errors typed like the router's adapter: a 429, overload, 5xx,
        timeout or dropped connection is ``Overloaded`` (retryable), so the node RetryPolicy
        retries it (W6; the SDK client's own retries are off)."""
        from llm.adapters.anthropic_api import map_error

        try:
            return await self.llm.complete(tier, system, user, max_tokens=max_tokens)
        except Exception as error:
            mapped = map_error(error)
            if mapped is error:
                raise
            raise mapped from error

    def payload_key(self, kind: str, name: str) -> str:
        """Neutral key under this run (no provider name, no account name)."""
        return (
            f"tenants/{self.tenant_id}/accounts/{self.account_id}/runs/{self.run_id}/{kind}/{name}"
        )
