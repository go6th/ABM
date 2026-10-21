"""Ask: workspace answers with numbered citations, the "What this answer used" summary, the
offer to refresh from the web (never taken without the user's confirmation) and follow-ups.

``POST /v1/chat/ask`` streams server-sent events: ``status`` (searching the workspace), ``text``
(paragraph index and final citation parts), ``thread`` (the thread the turn belongs to, created on
the first question), ``answer`` (the whole structured answer) and ``done``; or ``error``. Model
text is buffered until the complete answer passes the client output scrub and tool/person/provider
domain scans; no partial text is sent first. A repeated ``request_id`` replays the stored answer
through the same guard. Answers never reach the web or a data tool; with a model key set, Claude
writes the prose from the same rows and every sentence's citations are checked before composition
(``replica.model_answer``). The rows include the account's
research, the tracker, previous deliverables, the client's uploaded files (Internal) and the
user's own mail and drive.

Tabular questions (2026-10-17): before retrieval, ``replica.router`` decides whether the
question is a filter, count or lookup over the caller's ingested tables. A ``tabular`` turn
skips retrieval and gives the model the ``describe_table`` and ``query_table`` tools (bound to
the caller's accounts, ``replica.tools``) at temperature 0; ``mixed`` offers tools and
retrieval; ``prose`` is unchanged. A tenant whose ``run_python`` admin flag is on also gets the
sandboxed ``run_python`` tool on the tabular and mixed routes (60 seconds, no network, every
call logged by the code's sha256). Each turn logs ``ask.route`` with the route, its reason,
the tools called, the predicates (columns and operators only), the last row count, the
predicate the model relaxed and the rows deduplication removed; ``replica.obs`` counts routes
and empty results. Kimi K3 (Moonshot) and Claude both run the tool loop; a provider without
tools would answer a tabular question through retrieval, as before.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from account_intelligence.api.audit_log import audited_txn
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.routes.limits import limit_reached
from account_intelligence.api.routes.model_choice import refused
from account_intelligence.chat.credentials import (
    TenantKeyUnavailable,
    TenantModelKeyStore,
    encrypted_key_for_tenant,
)
from account_intelligence.chat.service import (
    AnthropicChatProvider,
    ChatService,
    ChatUsage,
    actual_cost,
    provider_for,
    sse,
)
from account_intelligence.chat.store import record_ask_turn
from account_intelligence.config import get_settings
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.llm import anthropic_api_client, chat_effort
from account_intelligence.replica import answers as A
from account_intelligence.replica import limits as LIM
from account_intelligence.replica import models as M
from account_intelligence.replica import obs as ask_obs
from account_intelligence.replica import router as R
from account_intelligence.replica.model_answer import refusal_phrase, stream_with_model
from account_intelligence.replica.render import ensure_table
from account_intelligence.replica.settings import touch_activity
from account_intelligence.replica.source_label import relabel_answer
from account_intelligence.replica.tools import AccessTools
from agents.naming import sanitize_client_value
from documents import tables as T
from llm.choice import catalogue
from obs.logging import logger

log = logger(__name__)

router = APIRouter(tags=["ask"])
register("POST", "/v1/chat/ask", tenant_route("read_public", "run", step_up=False))

LOCAL_MODEL = "workspace-local"


class AskBody(BaseModel):
    """One question. The tenant and user come from the session, never from the body."""

    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    question: str = Field(min_length=1, max_length=4000)
    thread_id: UUID | None = None
    account_id: UUID | None = None
    # the analysis model for this conversation (the composer's model chip); None keeps the
    # conversation's model, else the user's current analysis model
    model: str | None = Field(default=None, min_length=1, max_length=64)
    # the effort for this question (medium or high, the composer's effort chip; owner decision
    # 2026-10-20); None: the user's analysis effort
    effort: str | None = Field(default=None, min_length=1, max_length=16)


def week_of(now: datetime) -> date:
    """The Monday of the week (the question counts' bucket)."""
    return (now - timedelta(days=now.weekday())).date()


async def _replay(tenant_id: UUID, thread_id: UUID, request_id: UUID) -> dict[str, Any] | None:
    async with tenant_txn(tenant_id) as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, answer_json FROM chat_turns WHERE thread_id = :t AND request_id ="
                    " :r"
                ),
                {"t": thread_id, "r": request_id},
            )
        ).first()
    if row is None:
        return None
    return {"turn_id": str(row.id), "answer": relabel_answer(row.answer_json)}


@router.post("/chat/ask")
async def ask(
    body: AskBody, request: Request, ident: Identity = Depends(authorize)
) -> StreamingResponse:
    """Answer one question from the workspace and stream the structured answer."""
    now = datetime.now(UTC)
    async with tenant_txn(ident.tenant_id) as session:
        if body.thread_id is not None:
            owned = await session.scalar(
                text("SELECT 1 FROM chat_threads WHERE id = :t AND owner_user_id = :u"),
                {"t": body.thread_id, "u": ident.user_id},
            )
            if not owned:
                raise APIProblem(404, "not_found", "Not found", "not found")
        if body.account_id is not None:
            held = await session.scalar(
                text("SELECT 1 FROM accounts WHERE id = :a"), {"a": body.account_id}
            )
            if not held:
                raise APIProblem(404, "not_found", "Not found", "not found")
        try:
            analysis_model = await M.conversation_model(
                session, ident.user_id, body.model, body.thread_id
            )
            turn_effort = (
                await M.resolve(session, ident.user_id, analysis_effort=body.effort)
            ).analysis_effort
        except M.ModelRefused as error:
            raise refused(error) from None
    replay = (
        await _replay(ident.tenant_id, body.thread_id, body.request_id) if body.thread_id else None
    )
    if replay is None:  # a question is one of the user's chats: refused before it starts
        async with tenant_txn(ident.tenant_id) as session:
            try:
                await LIM.check(session, ident.user_id, "chats", now)
            except LIM.LimitReached as reached:
                raise limit_reached(reached) from None

    async def events() -> AsyncIterator[bytes]:
        """Stream the answer: replay a repeated request, or search, compose, store and send."""
        turn_started = time.monotonic()
        if replay is not None:
            try:
                replay_answer = cast(dict[str, Any], sanitize_client_value(replay["answer"] or {}))
            except Exception:  # noqa: BLE001 - a stored answer that fails the gate is withheld
                yield sse(
                    "error",
                    {
                        "code": "answer_failed",
                        "message": "The answer could not be completed. Try again.",
                    },
                )
                return
            yield sse("thread", {"thread_id": str(body.thread_id), "created": False})
            yield sse("answer", replay_answer)
            yield sse("done", {"turn_id": replay["turn_id"], "replayed": True})
            return
        yield sse("status", {"state": "searching"})
        owned_client = None
        try:
            service = getattr(request.app.state, "chat_service", None)
            provider, owned_client = await _provider_for_ask(
                request, service, analysis_model, ident.tenant_id
            )
            tools: AccessTools | None = None
            classifier: Any = None
            # the model the answer was paid on, kept even when its reply is not used
            paid_model: str | None = None
            decision = R.Decision("prose", "no table in the workspace")
            found: list[T.TableInfo] = []
            async with tenant_txn(ident.tenant_id) as session:
                ws = await A.load_workspace(
                    session,
                    ident.user_id,
                    body.account_id,
                    include_closed=A.wants_closed(body.question),
                )
                docs = getattr(request.app.state.container, "documents", None)
                searched = {body.account_id} if body.account_id else {a.id for a in ws.accounts}
                semantic = getattr(docs, "semantic", None)
                # is the question a filter, count or lookup over the caller's tables?
                try:
                    found = await T.list_tables(session, sorted(searched))
                    classifier = _classifier(request, provider, analysis_model)
                    decision = await R.route(body.question, found, classifier)
                    if decision.route != "prose" and not getattr(provider, "supports_tools", False):
                        decision = R.Decision(
                            "prose", f"{decision.reason}; the model's provider offers no tools"
                        )
                    if decision.route != "prose":
                        ws.tables = A.table_rows(ws, found)
                    if getattr(provider, "supports_tools", False):
                        tools = AccessTools(
                            lambda: tenant_txn(ident.tenant_id),
                            found,
                            sorted(searched),
                            semantic,
                            run_python=await _run_python_on(session, ident.tenant_id),
                        )
                except Exception:  # noqa: BLE001 - an answer without tables still stands
                    log.warning("ask_tables_failed", extra={"request_id": str(body.request_id)})
                    decision, tools = R.Decision("prose", "the table store failed"), None
                # the passages of the caller's own documents that best answer this question
                # (hybrid search; Ask never read the files before, audit 2026-10-15); a tabular
                # question is answered from the table store instead, never from a sample
                if decision.route != "tabular":
                    try:
                        async with session.begin_nested():  # a failure rolls back only this
                            ws.passages = await A.passage_rows(
                                session,
                                ws,
                                body.question,
                                searched,
                                getattr(docs, "semantic", None),
                            )
                            # the file the question is about goes to the model whole
                            await A.take_whole_file(
                                session, ws, get_settings().ask_whole_file_max_chars
                            )
                    except Exception:  # noqa: BLE001 - an answer without passages still stands
                        log.warning(
                            "ask_passages_failed", extra={"request_id": str(body.request_id)}
                        )
            scope = A.scope_account(ws, body.account_id)
            q, draft = A.draft_local(body.question, ws, now, scope)
            model_id = LOCAL_MODEL
            usage = None
            from_model = False
            log.info(
                "ask.gate",
                extra={
                    "request_id": str(body.request_id),
                    "has_chat_service": isinstance(service, ChatService),
                    "provider_resolved": provider is not None,
                    "provider_type": type(provider).__name__ if provider else None,
                    "has_accounts": bool(ws.accounts),
                    "analysis_model": analysis_model,
                    "effort": turn_effort,
                },
            )
            if isinstance(service, ChatService) and provider is not None and ws.accounts:
                try:
                    model = service.registry.resolve(analysis_model)
                    paid_model = model.id
                    log.info(
                        "ask.model_call_start",
                        extra={"request_id": str(body.request_id), "model_id": model.id},
                    )
                    async for kind, value in stream_with_model(
                        provider,
                        model,
                        q,
                        ws,
                        draft,
                        user_id=ident.user_id,
                        request_id=str(body.request_id),
                        tools=tools.offered(decision.route) if tools is not None else None,
                        run_tool=tools.run if tools is not None else None,
                        temperature=0.0 if tools is not None else None,
                        effort=turn_effort,
                    ):
                        if kind == "piece":
                            # Hold streamed pieces until the whole answer is scrubbed and scanned.
                            # No partial phrase can escape before the complete scan.
                            continue
                        elif kind == "discard":  # text before a tool call is never sent
                            continue
                        elif kind == "tool":
                            continue  # counted by the tools themselves
                        elif kind == "done":
                            draft, usage, model_id = value.draft, value.usage, model.id
                            from_model = True
                            log.info(
                                "ask.model_call_done",
                                extra={
                                    "request_id": str(body.request_id),
                                    "model_id": model.id,
                                    "has_usage": usage is not None,
                                },
                            )
                        else:  # the reply could not be used: the local answer follows
                            usage = value
                            log.warning(
                                "ask.model_call_reset",
                                extra={
                                    "request_id": str(body.request_id),
                                    "reason": "provider returned reset",
                                    # a cut-off reply shows as stop_reason max_tokens
                                    "output_tokens": getattr(usage, "output_tokens", None),
                                    "input_tokens": getattr(usage, "input_tokens", None),
                                    "stop_reason": getattr(usage, "stop_reason", None),
                                },
                            )
                except Exception as e:  # the local answer stands; no user text is logged
                    log.exception("ask_model_fallback")
                    log.warning(
                        "ask.gate_fallback",
                        extra={
                            "request_id": str(body.request_id),
                            "reason": "exception_in_stream_with_model",
                            "exception_type": type(e).__name__,
                            "exception_msg": str(e)[:200],
                        },
                    )
                    # Keep the local records, but disclose that the model answer was unavailable.
                    _mark_local_fallback(draft)
            else:
                log.warning(
                    "ask.gate_fallback",
                    extra={
                        "request_id": str(body.request_id),
                        "reason": "gate_failed",
                        "has_chat_service": isinstance(service, ChatService),
                        "provider_resolved": provider is not None,
                        "has_accounts": bool(ws.accounts),
                    },
                )
            # the table guarantee's last line: whatever wrote the answer, it leaves with a table
            ensure_table(draft)
            answer = A.finish(draft, q, ws, now, scope)
            stats = tools.stats() if tools is not None else {}
            cost = _turn_cost(service, paid_model, usage, classifier)
            log.info(
                "ask.route",
                extra={
                    "request_id": str(body.request_id),
                    "route": decision.route,
                    # the cost view's inputs (token review item 19, 2026-10-20)
                    "model_id": paid_model,
                    "input_tokens": getattr(usage, "input_tokens", 0),
                    "output_tokens": getattr(usage, "output_tokens", 0),
                    "cache_read_tokens": getattr(usage, "cache_read_tokens", 0),
                    "cache_write_tokens": getattr(usage, "cache_write_tokens", 0),
                    "cost_usd": str(cost),
                    "matched_columns": ",".join(decision.matched_columns)[:200],
                    "matched_values": ",".join(decision.matched_values)[:200],
                    "reason": decision.reason[:200],
                    "outcome": "model" if from_model else "local",
                    "latency_ms": round((time.monotonic() - turn_started) * 1000),
                    **stats,
                },
            )
            ask_obs.record_route(decision.route)
            if tools is not None:
                ask_obs.record_table_queries(
                    sum(1 for c in tools.calls if c["name"] == "query_table"),
                    int(stats.get("empty_results") or 0),
                )
            # "What this answer used" names the model that wrote it (none: the workspace's own)
            choice = catalogue().get(model_id) if from_model else None
            answer["used"]["model_name"] = (
                (choice.name if choice else model_id) if from_model else None
            )
            answer = cast(dict[str, Any], sanitize_client_value(answer))
            for index, paragraph in enumerate(answer["paragraphs"]):
                yield sse("text", {"p": index, "parts": paragraph})
            plain = A.plain_text(answer["paragraphs"])[:100_000] or " "
            # the refusal guard rail (the eval fails on the same phrases): with tables in the
            # workspace an answer must never send the user away to re-export a file
            if found and from_model and refusal_phrase(plain):
                log.warning(
                    "ask.answer_refusal",
                    extra={"request_id": str(body.request_id), "route": decision.route},
                )
            async with audited_txn(
                request,
                audit_class="run",
                action="chat_ask",
                resource_type="chat_thread",
                resource_id=body.thread_id,
            )(ident.tenant_id) as session:
                thread_id, turn_id, created = await record_ask_turn(
                    session,
                    user_id=ident.user_id,
                    thread_id=body.thread_id,
                    request_id=body.request_id,
                    model_id=model_id,
                    question=body.question,
                    plain_answer=plain,
                    account_id=body.account_id,
                    input_tokens=usage.input_tokens if usage else 0,
                    output_tokens=usage.output_tokens if usage else 0,
                    cache_read_tokens=usage.cache_read_tokens if usage else 0,
                    cache_write_tokens=usage.cache_write_tokens if usage else 0,
                    cost_usd=cost,
                    billed_model_id=paid_model,
                    answer_json=_json(answer),
                    week=week_of(now),
                    category=A.classify(body.question),
                )
                await touch_activity(session, ident.user_id, now)
                # counted with the answer it bought (a failed answer costs no chat)
                await LIM.take(session, ident.user_id, "chats", now, force=True)
                if body.model is not None:  # the conversation keeps the model it was given
                    await session.execute(
                        text("UPDATE chat_threads SET analysis_model = :m WHERE id = :t"),
                        {"m": analysis_model, "t": thread_id},
                    )
            yield sse("thread", {"thread_id": str(thread_id), "created": created})
            yield sse("answer", answer)
            yield sse("done", _done_frame(turn_id, model_id, usage, cost))
        except Exception:
            # the stream has started: any failure becomes an SSE error event (logged), not a 500
            log.exception("ask_failed")
            yield sse(
                "error",
                {
                    "code": "answer_failed",
                    "message": "The answer could not be completed. Try again.",
                },
            )
        finally:
            if owned_client is not None:
                try:
                    await owned_client.close()
                except Exception:
                    # Cleanup must not hide the streamed response or its original failure.
                    log.exception("ask_tenant_provider_close_failed")

    return StreamingResponse(
        _with_keepalive(events()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


# a relay drops a stream that is silent for 300 s (Node's body timeout in the web tier, 2026-10-19);
# while the model writes a long structured answer nothing else is sent, so a comment frame
# (ignored by every SSE reader, web/lib/sse.ts included) keeps the stream alive
KEEPALIVE_S = 15.0
KEEPALIVE = b": keepalive\n\n"


async def _with_keepalive(
    frames: AsyncIterator[bytes], every: float = KEEPALIVE_S
) -> AsyncIterator[bytes]:
    """The stream's frames, with a keepalive comment whenever it is silent for ``every``
    seconds. The source runs whole in one task, so its context (tenant transaction, request
    id) is the one it started with; closing this stream cancels that task."""
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for frame in frames:
                await queue.put(("frame", frame))
        except BaseException as error:  # noqa: BLE001 - re-raised on the reading side
            await queue.put(("error", error))
            return
        await queue.put(("end", None))

    task = asyncio.create_task(pump())
    try:
        # LOOP(ask keepalive): cap=the source stream's frames plus one keepalive per silent
        # interval; exit=the source ends or fails, or the client goes away
        while True:
            try:
                kind, value = await asyncio.wait_for(queue.get(), timeout=every)
            except TimeoutError:
                yield KEEPALIVE
                continue
            if kind == "end":
                return
            if kind == "error":
                raise value
            yield value
    finally:
        if not task.done():
            task.cancel()


def _provider_for(request: Request, service: Any, model_id: str) -> Any:
    """The streaming provider of the conversation's model (the shared helper of every chat
    surface; None when it is not set up: the local answer stands)."""
    return provider_for(getattr(request.app.state, "chat_providers", None), service, model_id)


async def _provider_for_ask(
    request: Request, service: Any, model_id: str, tenant_id: UUID
) -> tuple[Any, Any | None]:
    """Resolve the selected provider, preferring the tenant's saved key for Claude."""
    provider = _provider_for(request, service, model_id)
    choice = catalogue().get(model_id)
    if choice is None or choice.provider != "anthropic":
        return provider, None
    ciphertext = await encrypted_key_for_tenant(tenant_id)
    if ciphertext is None:
        return provider, None
    key_store = getattr(request.app.state, "tenant_model_keys", None)
    if not isinstance(key_store, TenantModelKeyStore) or not key_store.configured:
        raise TenantKeyUnavailable("tenant model key KMS is unavailable")
    api_key = await key_store.decrypt(tenant_id, ciphertext)
    try:
        client = anthropic_api_client(api_key)
    finally:
        del api_key
    return AnthropicChatProvider(client, effort=chat_effort()), client


def _mark_local_fallback(draft: A.Draft) -> None:
    """Prepend a notice without changing the local answer's citations or structured content."""
    draft.paragraphs.insert(0, "AI summary unavailable. Showing workspace records:")


def _classifier(request: Request, provider: Any, model_id: str) -> Any:
    """The fallback route classifier (``ASK_ROUTE_LLM_FALLBACK``) on the helper model of
    ``models.yaml`` ``helpers.route_classifier`` (Haiku 5.5), over the container's Anthropic
    client; None when the conversation is not on a Claude model (Moonshot), there is no client,
    no helper is configured, or it is off."""
    from account_intelligence.platform.llm import load_helper

    if get_settings().ask_route_llm_fallback != "on":
        return None
    choice = catalogue().get(model_id)
    if choice is None or choice.provider != "anthropic" or provider is None:
        return None
    client = getattr(getattr(request.app.state.container, "llm", None), "client", None)
    helper = load_helper("route_classifier")
    if client is None or helper is None:
        return None
    return R.LlmRouteClassifier(client, helper.model, helper.max_tokens)


def _done_frame(turn_id: Any, model_id: str, usage: Any, cost: Decimal) -> dict[str, Any]:
    """The last frame of a new answer: its id and what it used, for the token meter in Ask. A
    workspace answer (no model) reports zero tokens and its cost."""
    return {
        "turn_id": str(turn_id),
        "replayed": False,
        "model": model_id,
        "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
        "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
        "cache_read_tokens": int(getattr(usage, "cache_read_tokens", 0) or 0),
        "cache_write_tokens": int(getattr(usage, "cache_write_tokens", 0) or 0),
        "cost_usd": str(cost),
    }


def _turn_cost(service: Any, paid: Any, usage: Any, classifier: Any) -> Decimal:
    """What the turn cost: the answer's usage at its model's prices (also when the reply was
    not used: it was paid) plus the route classifier's call at the helper model's prices.
    A model the registry does not price counts zero and is logged."""
    total = Decimal(0)
    pairs = [(paid, usage)]
    if classifier is not None and getattr(classifier, "usage", None) is not None:
        u = classifier.usage
        pairs.append(
            (
                classifier.model_id,
                ChatUsage(
                    int(getattr(u, "input_tokens", 0) or 0),
                    int(getattr(u, "output_tokens", 0) or 0),
                    int(getattr(u, "cache_read_input_tokens", 0) or 0),
                    int(getattr(u, "cache_creation_input_tokens", 0) or 0),
                ),
            )
        )
    for model_id, used in pairs:
        if model_id is None or used is None or not isinstance(service, ChatService):
            continue
        try:
            total += actual_cost(service.registry.resolve(model_id), used)
        except Exception:  # noqa: BLE001 - an unpriced model never fails the answer
            log.warning("ask.cost_unpriced", extra={"model_id": str(model_id)})
    return total


async def _run_python_on(session: Any, tenant_id: UUID) -> bool:
    """Whether this tenant may use the run_python sandbox tool (the admin flag, deny by
    default; a flags failure turns it off, never on)."""
    try:
        from admin import flags
        from metering import limits as metering

        rule = await flags.current(session, "run_python")
        if not rule:
            return False
        return flags.evaluate(rule, "run_python", tenant_id, await metering.plan_code(session))
    except Exception:  # noqa: BLE001 - deny by default
        return False


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
