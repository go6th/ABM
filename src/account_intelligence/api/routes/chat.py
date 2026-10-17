"""Tenant-private chat, model discovery and bounded streaming replies."""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from account_intelligence.api import contract as C
from account_intelligence.api.audit_log import audit_action, audited_txn
from account_intelligence.api.auth import Identity
from account_intelligence.api.errors import APIProblem
from account_intelligence.api.guard import authorize
from account_intelligence.api.policy import register, tenant_route
from account_intelligence.api.problem import DependencyUnavailable
from account_intelligence.chat.credentials import (
    TenantKeyUnavailable,
    TenantModelKeyStore,
    delete_encrypted_key,
    encrypted_key_for_thread,
    key_status,
    save_encrypted_key,
)
from account_intelligence.chat.grounding import workspace_context
from account_intelligence.chat.registry import ChatConfigInvalid, get_chat_registry
from account_intelligence.chat.service import (
    AnthropicChatProvider,
    ChatService,
    ChatUsage,
    OutOfContextError,
    provider_for,
    sse,
    validate_project_context,
)
from account_intelligence.chat.store import (
    ThreadBusy,
    ThreadLimitReached,
    ThreadNotFound,
    complete_turn,
    create_thread,
    delete_thread,
    fail_turn,
    get_thread,
    list_threads,
    reserve_turn,
)
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.llm import anthropic_api_client, chat_effort
from account_intelligence.replica import models as M
from llm.choice import catalogue

router = APIRouter(tags=["chat"])
register("GET", "/v1/models", tenant_route("read_public", "read", step_up=False))
register(
    "GET",
    "/v1/chat/provider-key",
    tenant_route("manage_settings", "read", step_up=False),
)
register("PUT", "/v1/chat/provider-key", tenant_route("manage_settings", "admin"))
register("DELETE", "/v1/chat/provider-key", tenant_route("manage_settings", "admin"))
register("POST", "/v1/chat/threads", tenant_route("start_run", "run"))
register("GET", "/v1/chat/threads", tenant_route("read_public", "read", step_up=False))
register("GET", "/v1/chat/threads/{thread_id}", tenant_route("read_public", "read", step_up=False))
register(
    "DELETE", "/v1/chat/threads/{thread_id}", tenant_route("save_own_view", "none", step_up=False)
)
register(
    "POST",
    "/v1/chat/threads/{thread_id}/turns/stream",
    tenant_route("start_run", "run"),
)


class NewThread(BaseModel):
    """No caller-supplied tenant, owner or budget fields."""

    model_config = ConfigDict(extra="forbid")


class NewTurn(BaseModel):
    """One append-only user turn. Model selection is handled automatically by the backend router."""

    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    content: str = Field(min_length=1, max_length=60000)
    account_id: UUID | None = None
    enforce_project_scope: bool = Field(
        default=True,
        description="Reject queries outside the domain of ABM and account intelligence.",
    )
    fresh_output: bool = Field(
        default=True,
        description="Guarantee fresh, non-cached output unique to this user.",
    )


class TenantModelKeyBody(BaseModel):
    """Admin-supplied BYO key; it is never returned or written to logs."""

    model_config = ConfigDict(extra="forbid")
    api_key: SecretStr

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        """Accept only a bounded Anthropic key shape without ever exposing it in errors."""
        secret = value.get_secret_value()
        if not 27 <= len(secret) <= 512 or not re.fullmatch(r"sk-ant-[A-Za-z0-9_-]+", secret):
            raise ValueError("invalid Anthropic API key")
        return value


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


@router.get("/chat/provider-key", response_model=C.TenantModelKeyStatus)
async def provider_key_status(
    request: Request, ident: Identity = Depends(authorize)
) -> dict[str, Any]:
    """Return only whether a BYO key is set and when it was last changed."""
    store = getattr(request.app.state, "tenant_model_keys", None)
    if not isinstance(store, TenantModelKeyStore) or not store.configured:
        return {"available": False, "configured": False, "updated_at": None}
    try:
        status = await key_status(ident.tenant_id)
    except Exception:
        # the key store (KMS and database) may fail in any way: 503, never key material
        raise APIProblem(
            503, "key_store_unavailable", "Key storage unavailable", "Try again later."
        ) from None
    return {"available": True, "configured": status.configured, "updated_at": status.updated_at}


@router.put("/chat/provider-key", response_model=C.TenantModelKeyStatus)
async def set_provider_key(
    body: TenantModelKeyBody,
    request: Request,
    ident: Identity = Depends(authorize),
) -> dict[str, Any]:
    """Encrypt a tenant's optional Anthropic key with KMS; audit without its value."""
    store = getattr(request.app.state, "tenant_model_keys", None)
    if not isinstance(store, TenantModelKeyStore) or not store.configured:
        raise DependencyUnavailable()
    secret = body.api_key.get_secret_value()
    if not secret.startswith("sk-ant-") or any(ord(ch) < 33 for ch in secret):
        raise APIProblem(422, "invalid_provider_key", "Key refused", "Enter a valid Anthropic key.")
    try:
        ciphertext = await store.encrypt(ident.tenant_id, secret)
    except TenantKeyUnavailable:
        raise DependencyUnavailable() from None
    finally:
        del secret
    async with tenant_txn(ident.tenant_id) as session:
        updated_at = await save_encrypted_key(session, ciphertext, ident.user_id)
        await audit_action(
            session,
            request,
            audit_class="admin",
            action="chat_provider_key_set",
            resource_type="provider_key",
            resource_id="anthropic",
        )
    return {"configured": True, "updated_at": updated_at}


@router.delete("/chat/provider-key", response_model=C.TenantModelKeyDeleted)
async def remove_provider_key(
    request: Request, ident: Identity = Depends(authorize)
) -> dict[str, bool]:
    """Remove encrypted BYO key bytes and audit the content-free administrative action."""
    async with tenant_txn(ident.tenant_id) as session:
        deleted = await delete_encrypted_key(session)
        await audit_action(
            session,
            request,
            audit_class="admin",
            action="chat_provider_key_removed",
            resource_type="provider_key",
            resource_id="anthropic",
        )
    return {"deleted": deleted}


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
        return await get_thread(ident.tenant_id, ident.user_id, thread_id)
    except ThreadNotFound:
        raise APIProblem(404, "not_found", "Not found", "not found") from None


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


@router.post("/chat/threads/{thread_id}/turns/stream")
async def stream_turn(
    thread_id: UUID,
    body: NewTurn,
    request: Request,
    ident: Identity = Depends(authorize),
) -> StreamingResponse:
    """Reserve a bounded turn before returning SSE, then persist token/cost attribution."""
    service = getattr(request.app.state, "chat_service", None)
    if not isinstance(service, ChatService) or service.provider is None:
        raise DependencyUnavailable()
    # The conversation's analysis model (owner decision 2026-10-17: follow-up chat reads the one
    # model layer every surface reads, like Ask and the research graph): the thread's kept model
    # while it is usable, else the user's current one — Kimi K3 included. Providers come from
    # the shared ProviderByModel, so a Moonshot conversation answers through Moonshot.
    async with tenant_txn(ident.tenant_id) as session:
        analysis_model = await M.conversation_model(session, ident.user_id, None, thread_id)
    model = service.registry.resolve(analysis_model)

    # Enforce project/domain scope: reject off-topic queries outside ABM and account intelligence
    if body.enforce_project_scope:
        try:
            validate_project_context(body.content, strict=True)
        except OutOfContextError as err:
            raise APIProblem(
                422,
                "out_of_context",
                "Query out of project context",
                str(err),
            ) from None

    try:
        reservation = await reserve_turn(
            ident.tenant_id,
            ident.user_id,
            thread_id,
            body.request_id,
            body.content,
            model,
            service.registry,
            service,
            body.account_id,
            txn=audited_txn(
                request,
                audit_class="run",
                action="chat_turn",
                resource_type="chat_thread",
                resource_id=thread_id,
            ),
        )
    except ThreadNotFound:
        raise APIProblem(404, "not_found", "Not found", "not found") from None
    except ThreadLimitReached:
        raise APIProblem(
            429,
            "chat_limit_reached",
            "Conversation limit reached",
            "This conversation reached its turn or spend limit.",
        ) from None
    except ThreadBusy:
        raise APIProblem(
            409,
            "chat_busy",
            "Response in progress",
            "This request is already running or has an idempotency conflict.",
        ) from None
    except ValueError:
        raise APIProblem(
            422, "invalid_message", "Message cannot be sent", "Check the message and try again."
        ) from None

    turn_provider = provider_for(
        getattr(request.app.state, "chat_providers", None), service, analysis_model
    )
    owned_provider = False
    owned_client: Any | None = None
    if not reservation.is_replay:
        # A tenant's own Anthropic key can serve a Claude conversation, never Kimi K3.
        choice = catalogue().get(analysis_model)
        if choice is not None and choice.provider == "anthropic":
            key_store = getattr(request.app.state, "tenant_model_keys", None)
            try:
                ciphertext = await encrypted_key_for_thread(
                    ident.tenant_id, ident.user_id, thread_id
                )
                if ciphertext is not None:
                    if not isinstance(key_store, TenantModelKeyStore) or not key_store.configured:
                        raise TenantKeyUnavailable("tenant model key KMS is unavailable")
                    tenant_key = await key_store.decrypt(ident.tenant_id, ciphertext)
                    owned_client = anthropic_api_client(tenant_key)
                    tenant_key = ""
                    turn_provider = AnthropicChatProvider(owned_client, effort=chat_effort())
                    owned_provider = True
            except asyncio.CancelledError:
                await fail_turn(ident.tenant_id, reservation, charge_estimate=False)
                raise
            except Exception:
                # release the reserved turn on any provider failure, then answer 503
                await fail_turn(ident.tenant_id, reservation, charge_estimate=False)
                raise DependencyUnavailable() from None
    if turn_provider is None:
        await fail_turn(ident.tenant_id, reservation, charge_estimate=False)
        raise DependencyUnavailable()
    grounding = ""
    if not reservation.is_replay:
        try:
            grounding = await workspace_context(
                ident.tenant_id,
                [m.content for m in reservation.messages if m.role == "user"],
                reservation.account_id,
            )
        except Exception:
            # release the reserved turn on any retrieval failure, then answer 503
            await fail_turn(ident.tenant_id, reservation, charge_estimate=False)
            raise DependencyUnavailable() from None

    async def events() -> AsyncIterator[bytes]:
        """Stream sanitized chunks, save the final response, and charge failures conservatively."""
        if reservation.is_replay:
            replay_usage = reservation.replay_usage
            if replay_usage is None:
                raise RuntimeError("replay usage is missing")
            yield sse("text", {"text": reservation.replay_content or ""})
            yield sse(
                "done",
                {
                    "turn_id": str(reservation.turn_id),
                    "model": reservation.model.id,
                    "input_tokens": replay_usage.input_tokens,
                    "output_tokens": replay_usage.output_tokens,
                    "cost_usd": str(reservation.replay_cost_usd or Decimal(0)),
                    "replayed": True,
                },
            )
            return

        output: list[str] = []
        output_chars = 0
        usage: ChatUsage | None = None
        completed = False
        try:
            async for event in turn_provider.stream(
                reservation.model,
                reservation.messages,
                service.registry.limits.max_output_tokens,
                user_id=str(ident.user_id),
                request_id=str(body.request_id),
                fresh_output=body.fresh_output,
                context=grounding,
            ):
                if event.kind == "text":
                    output_chars += len(event.text)
                    if output_chars > 100_000:
                        raise ValueError("provider output exceeded the response bound")
                    output.append(event.text)
                    yield sse("text", {"text": event.text})
                elif event.kind == "usage":
                    usage = event.usage
            if usage is None:
                raise ValueError("provider completed without usage")
            if usage.output_tokens > service.registry.limits.max_output_tokens:
                raise ValueError("provider exceeded the configured output token limit")
            answer = "".join(output)
            cost = await complete_turn(ident.tenant_id, reservation, answer, usage)
            completed = True
            yield sse(
                "done",
                {
                    "turn_id": str(reservation.turn_id),
                    "model": reservation.model.id,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_tokens": usage.cache_read_tokens,
                    "cache_write_tokens": usage.cache_write_tokens,
                    "cost_usd": str(cost),
                    "replayed": False,
                },
            )
        except asyncio.CancelledError:
            if not completed:
                await fail_turn(ident.tenant_id, reservation)
            raise
        except Exception:
            # the stream has started: release the turn and send an SSE error event instead
            if not completed:
                await fail_turn(ident.tenant_id, reservation)
            yield sse(
                "error",
                {
                    "code": "provider_error",
                    "message": "The model response could not be completed.",
                },
            )
        finally:
            if owned_provider and owned_client is not None:
                await owned_client.close()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
