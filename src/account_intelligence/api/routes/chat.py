"""Tenant-private chat threads and model discovery. The BYO provider-key routes and the old chat
stream route had no web caller and were parked (ADR-0136); Ask (``routes/ask.py``) answers
questions."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audited_txn
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.problem import DependencyUnavailable
from account_intelligence.chat.registry import ChatConfigInvalid, get_chat_registry
from account_intelligence.chat.store import (
    ThreadBusy,
    ThreadNotFound,
    create_thread,
    delete_thread,
    get_thread,
    list_threads,
)
from account_intelligence.replica.source_label import relabel_answer
from obs.logging import logger

log = logger(__name__)
router = APIRouter(tags=["chat"])
register("GET", "/v1/models", tenant_route("read_public", "read", step_up=False))
register("POST", "/v1/chat/threads", tenant_route("start_run", "run"))
register("GET", "/v1/chat/threads", tenant_route("read_public", "read", step_up=False))
register("GET", "/v1/chat/threads/{thread_id}", tenant_route("read_public", "read", step_up=False))
register(
    "DELETE", "/v1/chat/threads/{thread_id}", tenant_route("save_own_view", "none", step_up=False)
)


class NewThread(BaseModel):
    """No caller-supplied tenant, owner or budget fields."""

    model_config = ConfigDict(extra="forbid")


@router.get("/models", response_model=C.ChatModels)
async def models() -> dict[str, Any]:
    """The current allowlist and public costs; no provider credential is returned."""
    try:
        registry = get_chat_registry()
    except ChatConfigInvalid as error:
        raise DependencyUnavailable() from error
    return {
        "default_model": registry.default_model,
        "models": [
            model.public(output_limit=registry.limits.max_output_tokens)
            for model in registry.models.values()
        ],
    }


@router.post("/chat/threads", response_model=C.ChatThreadCreated, status_code=201)
async def new_thread(
    _body: NewThread, request: Request, ident: Identity = Depends(authorize)
) -> dict[str, str]:
    """Create an empty private history owned by the verified user and tenant."""
    txn = audited_txn(
        request,
        audit_class="run",
        action="chat_thread_create",
        resource_type="chat_thread",
        resource_id=None,
    )
    return {"id": str(await create_thread(ident.tenant_id, ident.user_id, txn=txn))}


@router.get("/chat/threads", response_model=C.ChatThreads)
async def threads(ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """List only this member's 100 most recently active threads."""
    return {"threads": await list_threads(ident.tenant_id, ident.user_id)}


@router.get("/chat/threads/{thread_id}", response_model=C.ChatThread)
async def thread(thread_id: UUID, ident: Identity = Depends(authorize)) -> dict[str, Any]:
    """Load a caller-owned conversation; other tenants and members receive a 404."""
    try:
        found = await get_thread(ident.tenant_id, ident.user_id, thread_id)
    except ThreadNotFound:
        raise APIProblem(404, "not_found", "Not found", "not found") from None
    # saved answers' sources read "6th / <site>" and open the original page (source_label)
    for turn in found.get("turns") or ():
        turn["answer"] = stored_answer(relabel_answer(turn.get("answer")))
    return found


def stored_answer(answer: Any) -> Any:
    """A saved answer as the thread route may return it, or None when it no longer fits the
    contract. ``answer_json`` is written by the build that answered and read by every later one;
    the contract is closed, so a key one build writes and another does not know (staging
    2026-11-04: ``context_tokens``) made every GET of the thread a 500. The turn's prose
    (``assistant_content``) is kept; only that turn's cards are left out, counted in the log."""
    if answer is None:
        return None
    try:
        C.ChatAnswer.model_validate(answer)
    except ValidationError as error:
        first = error.errors()[0]
        log.warning(
            "chat.stored_answer_dropped",
            extra={
                "count": error.error_count(),
                "error_code": ".".join(str(p) for p in first["loc"]) or first["type"],
            },
        )
        return None
    return answer


@router.delete("/chat/threads/{thread_id}", response_model=C.ChatThreadDeleted)
async def remove_thread(thread_id: UUID, ident: Identity = Depends(authorize)) -> dict[str, bool]:
    """Delete a caller-owned conversation and its stored transcript."""
    try:
        await delete_thread(ident.tenant_id, ident.user_id, thread_id)
    except ThreadNotFound:
        raise APIProblem(404, "not_found", "Not found", "not found") from None
    except ThreadBusy:
        raise APIProblem(
            409, "chat_busy", "Response in progress", "Wait for the current response to finish."
        ) from None
    return {"deleted": True}
