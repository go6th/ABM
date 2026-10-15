"""Ask: workspace answers with numbered citations, the "What this answer used" summary, the
offer to refresh from the web (never taken without the user's confirmation) and follow-ups.

``POST /v1/chat/ask`` streams server-sent events: ``status`` (searching the workspace), ``text``
(the answer as it is written, one sentence at a time: paragraph index and parts, with citation
numbers already final), ``reset`` (the model's reply could not be used: forget the text so far,
the local answer follows), ``thread`` (the thread the turn belongs to, created on the first
question), ``answer`` (the whole structured answer, the same text) and ``done``; or ``error``. A
repeated ``request_id`` replays the stored answer. Answers never reach the web or a data tool;
with a model key set, Claude writes the prose from the same rows and every sentence's citations
are checked before it is sent (``replica.model_answer``). The rows include the account's
research, the tracker, previous deliverables, the client's uploaded files (Internal) and the
user's own mail and drive.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
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
from account_intelligence.chat.service import ChatService, sse
from account_intelligence.chat.store import record_ask_turn
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import answers as A
from account_intelligence.replica import limits as LIM
from account_intelligence.replica import models as M
from account_intelligence.replica.model_answer import stream_with_model
from account_intelligence.replica.settings import touch_activity
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
    return None if row is None else {"turn_id": str(row.id), "answer": row.answer_json}


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
        if replay is not None:
            yield sse("thread", {"thread_id": str(body.thread_id), "created": False})
            yield sse("answer", replay["answer"] or {})
            yield sse("done", {"turn_id": replay["turn_id"], "replayed": True})
            return
        yield sse("status", {"state": "searching"})
        try:
            async with tenant_txn(ident.tenant_id) as session:
                ws = await A.load_workspace(session, ident.user_id, body.account_id)
                # the passages of the caller's own documents that best answer this question
                # (hybrid search; Ask never read the files before, audit 2026-10-15)
                docs = getattr(request.app.state.container, "documents", None)
                searched = {body.account_id} if body.account_id else {a.id for a in ws.accounts}
                try:
                    async with session.begin_nested():  # a failure rolls back only this
                        ws.passages = await A.passage_rows(
                            session, ws, body.question, searched, getattr(docs, "semantic", None)
                        )
                except Exception:  # noqa: BLE001 - an answer without passages still stands
                    log.warning("ask_passages_failed", extra={"request_id": str(body.request_id)})
            scope = A.scope_account(ws, body.account_id)
            q, draft = A.draft_local(body.question, ws, now, scope)
            model_id = LOCAL_MODEL
            usage = None
            numbering: dict[str, int] = {}
            streamed = False
            from_model = False
            service = getattr(request.app.state, "chat_service", None)
            provider = _provider_for(request, service, analysis_model)
            if isinstance(service, ChatService) and provider is not None and ws.accounts:
                try:
                    model = service.registry.resolve(analysis_model)
                    async for kind, value in stream_with_model(
                        provider,
                        model,
                        q,
                        ws,
                        draft,
                        user_id=ident.user_id,
                        request_id=str(body.request_id),
                    ):
                        if kind == "piece":
                            index, piece = value
                            parts = A.number_marks(piece, numbering)
                            yield sse("text", {"p": index, "parts": parts})
                            streamed = True
                        elif kind == "done":
                            draft, usage, model_id = value.draft, value.usage, model.id
                            from_model = True
                        else:  # the reply could not be used: the local answer follows
                            usage = value
                except Exception:  # the local answer stands; no user text is logged
                    log.warning("ask_model_fallback", extra={"request_id": str(body.request_id)})
            if not from_model:
                if streamed:
                    yield sse("reset", {})
                numbering = {}
                for index, para in enumerate(draft.paragraphs):
                    for piece in A.pieces(para):
                        yield sse("text", {"p": index, "parts": A.number_marks(piece, numbering)})
            answer = A.finish(draft, q, ws, now, scope)
            # "What this answer used" names the model that wrote it (none: the workspace's own)
            choice = catalogue().get(model_id) if from_model else None
            answer["used"]["model_name"] = (
                (choice.name if choice else model_id) if from_model else None
            )
            plain = A.plain_text(answer["paragraphs"])[:100_000] or " "
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
            yield sse("done", {"turn_id": str(turn_id), "replayed": False})
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

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


def _provider_for(request: Request, service: Any, model_id: str) -> Any:
    """The streaming provider of the conversation's model: Moonshot for Kimi K3, else the
    Anthropic provider (None when that provider is not set up: the local answer stands)."""
    choice = catalogue().get(model_id)
    providers = getattr(request.app.state, "chat_providers", None)
    if choice is not None and providers is not None:
        found = providers.for_model(choice.provider)
        if found is not None:
            return found
    if choice is not None and choice.provider != "anthropic":
        return None
    return getattr(service, "provider", None)


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
