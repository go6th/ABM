"""Model use for research files: estimate, gate, cache, call, log.

* Routing: a task names a tier in ``config/research_files.yaml``; the tier resolves to a model
  through ``config/models.yaml`` (the active arm), so cheap tasks go to the cheapest configured
  model and summaries to a stronger one. No model id is written in code.
* Cache: the key is the task, the model, the prompt version and the content hash. A key with a
  stored reply (in this or any other file of the tenant) is served from the log at $0.
* Estimate: characters / ``chars_per_token`` input plus ``max_tokens`` output per uncached call,
  priced from ``config/models.yaml``. Above ``approval_limit_usd`` the step waits for approval.
* Write-ahead: a ``started`` row before the request; the reply with tokens and cost right after
  it returns, before anything parses it.
* Prompt cap: text longer than ``max_prompt_chars`` is split; each piece is its own cached call.
* Stops: credits exhausted, refused keys, refusals, a missing model or a model that stays
  overloaded end the step with a reason (``ModelStop``); everything finished is kept.

GUARANTEE(test_a_second_run_of_the_same_chunks_costs_nothing)
GUARANTEE(test_the_model_step_waits_for_approval_above_the_limit)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from llm.tasks import complete_task
from research_files import repo
from research_files.checks import redact_secrets
from research_files.config import ResearchFilesConfig

TxnFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
PROMPT_VERSION = "rf-1"

SYSTEMS = {
    "summarize_section": (
        "You summarise one section of a document a user uploaded. Write two to four plain"
        " sentences that state what the section says, using only the text given. Do not add"
        ' facts, opinions or advice. Answer with one JSON object: {"summary": "..."}.'
    ),
    "answer_question": (
        "Answer the user's question using only the numbered passages from their file. After every"
        " statement, cite the passages it rests on as [C1], [C2] and so on. If the passages do not"
        " contain the answer, say that plainly. Do not use outside knowledge. Answer with one JSON"
        ' object: {"answer": "...", "citations": ["C1"]}.'
    ),
}


class ModelStop(RuntimeError):
    """The model step cannot continue; ``reason`` is the file's stop reason."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Item:
    """One planned call: the task, the content hash (cache identity) and the text to send."""

    task: str
    content_hash: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Estimate:
    """What a model step would cost before it runs."""

    calls: int
    cached: int
    usd: float
    model: str | None
    priced: bool
    needs_approval: bool
    limit_usd: float

    def to_json(self) -> dict[str, Any]:
        """Serialisable form."""
        return {
            "calls": self.calls,
            "cached": self.cached,
            "usd": round(self.usd, 6),
            "model": self.model,
            "priced": self.priced,
            "needs_approval": self.needs_approval,
            "limit_usd": self.limit_usd,
        }


def split_text(text: str, cap: int) -> list[str]:
    """Pieces of at most ``cap`` characters, cut at paragraph or line ends where possible."""
    if len(text) <= cap:
        return [text]
    out, start = [], 0
    # LOOP(prompt split): cap=len(text) / cap pieces; exit=text consumed
    while start < len(text):
        end = min(len(text), start + cap)
        cut = max(text.rfind("\n\n", start, end), text.rfind("\n", start, end))
        if cut <= start or end == len(text):
            cut = end
        out.append(text[start:cut])
        start = cut
    return out


class FileModels:
    """See the module docstring. ``llm`` is the platform ``LlmPort`` (None: no model)."""

    def __init__(self, llm: Any | None, cfg: ResearchFilesConfig, txn: TxnFactory) -> None:
        self.llm = llm
        self.cfg = cfg
        self.txn = txn

    @property
    def configured(self) -> bool:
        """Whether a model can be called."""
        return self.llm is not None

    def task_kind(self, task: str) -> str:
        """The registered task kind of a file task (``config/research_files.yaml``)."""
        return self.cfg.models.tasks[task]

    def max_tokens(self, task: str) -> int:
        """The task kind's output limit (``config/model_routing.yaml``)."""
        from llm.tasks import require

        return require(self.task_kind(task)).max_tokens

    def model_for(self, task: str) -> str:
        """The model id behind the task's tier (the tier name when the port has no routes)."""
        from llm.tasks import LEGACY_TIER

        tier = LEGACY_TIER[self.task_kind(task)]
        route = getattr(self.llm, "routes", {}).get(tier) if self.llm is not None else None
        return str(getattr(route, "model", "") or tier)

    def key(self, item: Item) -> str:
        """The cache key of a planned call."""
        model = self.model_for(item.task)
        body = json.dumps([PROMPT_VERSION, item.task, model, item.content_hash])
        return hashlib.sha256(body.encode()).hexdigest()

    def _user(self, item: Item) -> str:
        return json.dumps(item.payload, ensure_ascii=False, sort_keys=True)

    async def estimate(self, tenant_id: UUID, items: Sequence[Item], approved: bool) -> Estimate:
        """See the module docstring."""
        from llm.reply_cache import ReplyUsage, price

        cached = 0
        total = Decimal(0)
        priced = True
        model = None
        async with self.txn(tenant_id) as s:
            for item in items:
                if await repo.cached_reply(s, self.key(item)) is not None:
                    cached += 1
                    continue
                model = self.model_for(item.task)
                chars = len(SYSTEMS[item.task]) + len(self._user(item))
                usage = ReplyUsage(
                    int(chars / self.cfg.models.chars_per_token), self.max_tokens(item.task)
                )
                cost = price(model, usage)
                if cost is None:
                    priced = False
                else:
                    total += cost
        usd = float(total)
        limit = self.cfg.models.approval_limit_usd
        needs = (usd > limit or not priced) and not approved and cached < len(items)
        return Estimate(len(items), cached, usd, model, priced, needs, limit)

    async def call(self, tenant_id: UUID, file_id: UUID | None, item: Item) -> tuple[str, str]:
        """``(reply_text, served_from)``; the reply is stored before the caller parses it."""
        from llm.reply_cache import ReplyUsage, price

        key = self.key(item)
        model = self.model_for(item.task)
        async with self.txn(tenant_id) as s:
            hit = await repo.cached_reply(s, key)
            if hit is not None:
                await repo.log_call(
                    s, file_id=file_id, task=item.task, model=model, key=key, served_from="cache"
                )
                return str(hit.reply_text), "cache"
        if self.llm is None:
            raise ModelStop("model_not_configured", "No language model is configured.")
        async with self.txn(tenant_id) as s:
            await repo.log_call(
                s, file_id=file_id, task=item.task, model=model, key=key, served_from="started"
            )
        result = await self._complete(
            self.task_kind(item.task), SYSTEMS[item.task], self._user(item)
        )
        usage = ReplyUsage(
            int(result.usage.input_tokens),
            int(result.usage.output_tokens),
            int(result.usage.cache_read_input_tokens or 0),
            int(result.usage.cache_creation_input_tokens or 0),
        )
        async with self.txn(tenant_id) as s:
            await repo.log_call(
                s,
                file_id=file_id,
                task=item.task,
                model=model,
                key=key,
                served_from="model",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost=price(model, usage),
                reply=result.text,
            )
        return str(result.text), "model"

    async def _complete(self, task_kind: str, system: str, user: str) -> Any:
        """One request; retryable errors twice with backoff; terminal errors are ``ModelStop``."""
        from account_intelligence.platform.ports import NotConfigured
        from llm.adapters.anthropic_api import map_error
        from llm.errors import (
            AuthFailed,
            ContentRefused,
            ContextTooLong,
            ModelUnavailable,
            Overloaded,
            ProviderCreditsExhausted,
        )

        terminal: dict[type[BaseException], str] = {
            ProviderCreditsExhausted: "model_credits_exhausted",
            AuthFailed: "model_auth_failed",
            ContentRefused: "model_refused",
            ModelUnavailable: "model_unavailable",
            ContextTooLong: "prompt_too_long",
            NotConfigured: "model_not_configured",
        }
        # LOOP(file model retry): cap=3 attempts; exit=reply, terminal stop, retries spent
        for attempt in range(3):
            try:
                return await complete_task(self.llm, task_kind, system, user)
            except Exception as error:  # noqa: BLE001 - mapped to typed errors below
                mapped = map_error(error) if not isinstance(error, NotConfigured) else error
                for cls, reason in terminal.items():
                    if isinstance(mapped, cls):
                        raise ModelStop(reason, f"{type(mapped).__name__}") from error
                if isinstance(mapped, Overloaded) and attempt < 2:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                if isinstance(mapped, Overloaded):
                    raise ModelStop("model_overloaded", "The model stayed unavailable.") from error
                raise
        raise ModelStop("model_overloaded", "The model stayed unavailable.")  # pragma: no cover


def parse_json_reply(text: str) -> dict[str, Any] | None:
    """The JSON object inside a reply, or None."""
    from account_intelligence.agents.output import parse_json

    value = parse_json(text)
    return value if isinstance(value, dict) else None


def section_items(
    cfg: ResearchFilesConfig, sections: Sequence[dict[str, Any]], texts: dict[int, tuple[str, str]]
) -> list[Item]:
    """Summary calls for every section with enough text (split at the prompt cap). ``texts`` maps
    a unit's sequence number to ``(content, content_hash)``."""
    items = []
    for section in sections:
        content, digest = texts.get(int(section["seq"]), ("", ""))
        if len(content.split()) < 40:
            continue
        pieces = split_text(redact_secrets(content), cfg.models.max_prompt_chars)
        for n, piece in enumerate(pieces):
            items.append(
                Item(
                    "summarize_section",
                    f"{digest}:{n}" if len(pieces) > 1 else digest,
                    {"section": section.get("heading") or "", "text": piece, "seq": section["seq"]},
                )
            )
    return items
