"""Ask: workspace answers with numbered citations, the "What this answer used" summary, the
offer to refresh from the web (never taken without the user's confirmation) and follow-ups.

``POST /v1/chat/ask`` streams server-sent events: ``thread`` (the thread the turn belongs to,
created on the first question), ``status`` (searching the workspace), ``answer`` (the whole
structured answer) and ``done``; or ``error``. A repeated ``request_id`` replays the stored
answer. Answers never reach the web or a data tool; with a model key set, Claude writes the
prose from the same rows and its citations are checked (``replica.model_answer``).
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

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audited_txn
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.chat.service import ChatService, sse
from account_intelligence.chat.store import record_ask_turn
from account_intelligence.db.session import tenant_txn
from account_intelligence.replica import answers as A
from account_intelligence.replica.model_answer import compose_with_model
from account_intelligence.replica.settings import touch_activity
from obs.logging import logger

log = logger(__name__)

router = APIRouter(tags=["ask"])
register("GET", "/v1/chat/suggestions", tenant_route("read_public", "read", step_up=False))
register("POST", "/v1/chat/ask", tenant_route("read_public", "run", step_up=False))

LOCAL_MODEL = "workspace-local"


class AskBody(BaseModel):
    """One question. The tenant and user come from the session, never from the body."""

    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    question: str = Field(min_length=1, max_length=4000)
    thread_id: UUID | None = None
    account_id: UUID | None = None


def week_of(now: datetime) -> date:
    """The Monday of the week (the question counts' bucket)."""
    return (now - timedelta(days=now.weekday())).date()


@router.get("/chat/suggestions", response_model=C.ChatSuggestions)
async def chat_suggestions(ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """Up to five questions the workspace can answer well now, and the account count."""
    async with tenant_txn(ident.tenant_id) as session:
        ws = await A.load_workspace(session, ident.user_id)
    return {"suggestions": A.suggestions(ws, datetime.now(UTC)), "accounts": len(ws.accounts)}


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
    replay = (
        await _replay(ident.tenant_id, body.thread_id, body.request_id) if body.thread_id else None
    )

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
            scope = A.scope_account(ws, body.account_id)
            q, draft = A.draft_local(body.question, ws, now, scope)
            model_id = LOCAL_MODEL
            usage = None
            service = getattr(request.app.state, "chat_service", None)
            if isinstance(service, ChatService) and service.provider is not None and ws.accounts:
                try:
                    from account_intelligence.model_routing import PipelinePhase, get_model_router

                    model = service.registry.resolve(
                        get_model_router().select_model(PipelinePhase.CHAT).id
                    )
                    reply = await compose_with_model(
                        service.provider,
                        model,
                        q,
                        ws,
                        draft,
                        user_id=ident.user_id,
                        request_id=str(body.request_id),
                    )
                    if reply is not None:
                        draft, usage, model_id = reply.draft, reply.usage, model.id
                except Exception:  # the local answer stands; no user text is logged
                    log.warning("ask_model_fallback", extra={"request_id": str(body.request_id)})
            answer = A.finish(draft, q, ws, now, scope)
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
            yield sse("thread", {"thread_id": str(thread_id), "created": created})
            yield sse("answer", answer)
            yield sse("done", {"turn_id": str(turn_id), "replayed": False})
        except Exception:
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


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
