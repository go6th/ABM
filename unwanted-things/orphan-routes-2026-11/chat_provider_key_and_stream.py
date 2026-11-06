"""Parked 2026-11-03 (Prompt F, ADR-0136): the BYO provider-key routes (GET/PUT/DELETE
/v1/chat/provider-key) and the old chat stream (POST /v1/chat/threads/{thread_id}/turns/stream),
with the store, credential and grounding helpers only they called. No web screen called them
(docs/audits/orphan-routes.md). Original homes: src/account_intelligence/api/routes/chat.py,
src/account_intelligence/chat/store.py, src/account_intelligence/chat/credentials.py. Not importable
as is: restore the pieces to those files, with their imports, to bring a route back."""


# ---- from src/account_intelligence/api/routes/chat.py ----


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
        # the user's analysis effort (medium or high, owner decision 2026-10-20)
        turn_effort = (await M.current(session, ident.user_id))[0].analysis_effort
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
        """Buffer, guard and send a complete response; charge failures conservatively."""
        if reservation.is_replay:
            replay_usage = reservation.replay_usage
            if replay_usage is None:
                raise RuntimeError("replay usage is missing")
            try:
                replay_content = sanitize_client_value(reservation.replay_content or "")
            except GuardViolation:
                yield sse(
                    "error",
                    {
                        "code": "output_blocked",
                        "message": "The response could not be returned.",
                    },
                )
                return
            yield sse("text", {"text": replay_content})
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
                effort=turn_effort,
            ):
                if event.kind == "text":
                    output_chars += len(event.text)
                    if output_chars > 100_000:
                        raise ValueError("provider output exceeded the response bound")
                    output.append(event.text)
                elif event.kind == "usage":
                    usage = event.usage
            if usage is None:
                raise ValueError("provider completed without usage")
            if usage.output_tokens > service.registry.limits.max_output_tokens:
                raise ValueError("provider exceeded the configured output token limit")
            answer = sanitize_client_value("".join(output))
            if not isinstance(answer, str):
                raise TypeError("sanitized model output must be text")
            cost = await complete_turn(ident.tenant_id, reservation, answer, usage)
            completed = True
            yield sse("text", {"text": answer})
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


# ---- from src/account_intelligence/chat/store.py ----


class ThreadLimitReached(RuntimeError):
    """A configured turn or spend limit prevents another provider request."""


@dataclass(frozen=True)
class Reservation:
    """One durable turn reservation, including the prior context passed to the model."""

    thread_id: UUID
    turn_id: UUID
    request_id: UUID
    model: ChatModel
    turn_number: int
    account_id: UUID | None
    messages: tuple[ChatMessage, ...]
    estimated_cost_usd: Decimal
    spend_limit_usd: Decimal
    replay_content: str | None = None
    replay_usage: ChatUsage | None = None
    replay_cost_usd: Decimal | None = None

    @property
    def is_replay(self) -> bool:
        """Whether an idempotent completed turn should be replayed without provider cost."""
        return self.replay_content is not None and self.replay_usage is not None


async def reserve_turn(
    tenant_id: UUID,
    user_id: str,
    thread_id: UUID,
    request_id: UUID,
    new_text: str,
    model: ChatModel,
    registry: ChatRegistry,
    service: ChatService,
    account_id: UUID | None,
    *,
    txn: Any = tenant_txn,
) -> Reservation:
    """Lock a thread, enforce limits, persist user input and reserve worst-case token cost
    (``txn`` as in ``create_thread``)."""
    async with txn(tenant_id) as session:
        thread = (
            await session.execute(
                text(
                    "SELECT id, title, turn_count, spent_usd, reserved_usd FROM chat_threads"
                    " WHERE id = :t AND owner_user_id = :u FOR UPDATE"
                ),
                {"t": thread_id, "u": user_id},
            )
        ).first()
        if thread is None:
            raise ThreadNotFound

        existing = (
            await session.execute(
                text(
                    "SELECT id, turn_number, model_id, user_content, assistant_content, status,"
                    " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,"
                    " account_id, cost_usd"
                    " FROM chat_turns WHERE thread_id = :t AND request_id = :q"
                ),
                {"t": thread_id, "q": request_id},
            )
        ).first()
        if existing is not None:
            stored_account = None if existing.account_id is None else UUID(str(existing.account_id))
            if (
                existing.model_id != model.id
                or existing.user_content != new_text
                or stored_account != account_id
            ):
                raise ThreadBusy("request id was already used for different content")
            if existing.status != "completed" or existing.assistant_content is None:
                raise ThreadBusy("the previous turn did not complete")
            usage = ChatUsage(
                int(existing.input_tokens),
                int(existing.output_tokens),
                int(existing.cache_read_tokens),
                int(existing.cache_write_tokens),
            )
            return Reservation(
                thread_id,
                UUID(str(existing.id)),
                request_id,
                model,
                int(existing.turn_number),
                stored_account,
                (),
                Decimal(0),
                registry.limits.max_spend_usd_per_thread,
                str(existing.assistant_content),
                usage,
                Decimal(existing.cost_usd),
            )

        active = (
            await session.execute(
                text(
                    "SELECT 1 FROM chat_turns WHERE thread_id = :t AND status = 'reserved' LIMIT 1"
                ),
                {"t": thread_id},
            )
        ).first()
        if active is not None:
            raise ThreadBusy("a response is already in progress")
        if int(thread.turn_count) >= registry.limits.max_turns:
            raise ThreadLimitReached("conversation turn limit reached")

        prior = (
            await session.execute(
                text(
                    "SELECT user_content, assistant_content FROM chat_turns"
                    " WHERE thread_id = :t AND status = 'completed' ORDER BY turn_number"
                ),
                {"t": thread_id},
            )
        ).all()
        history: list[ChatMessage] = []
        for row in prior:
            history.append(ChatMessage("user", str(row.user_content)))
            history.append(ChatMessage("assistant", str(row.assistant_content)))
        messages = service.validate_messages(history, new_text, model)
        reserve = service.reserve_cost(model, messages)
        if Decimal(thread.spent_usd) + Decimal(thread.reserved_usd) + reserve > (
            registry.limits.max_spend_usd_per_thread
        ):
            raise ThreadLimitReached("conversation spend limit reached")
        if account_id is not None:
            account = (
                await session.execute(
                    text("SELECT 1 FROM accounts WHERE id = :a AND deleted_at IS NULL"),
                    {"a": account_id},
                )
            ).first()
            if account is None:
                raise ThreadNotFound

        turn_number = int(thread.turn_count) + 1
        created = await session.execute(
            text(
                "INSERT INTO chat_turns (thread_id, turn_number, request_id, model_id,"
                " user_content,"
                " client, account_id, phase, reserved_usd) VALUES (:t, :n, :q, :m, :u, 'web', :a,"
                " 'chat', :reserve) RETURNING id"
            ),
            {
                "t": thread_id,
                "n": turn_number,
                "q": request_id,
                "m": model.id,
                "u": new_text,
                "a": account_id,
                "reserve": reserve,
            },
        )
        turn_id = UUID(str(created.scalar_one()))
        title = _title(new_text) if not thread.title else thread.title
        await session.execute(
            text(
                "UPDATE chat_threads SET turn_count = :n, reserved_usd = reserved_usd + :reserve,"
                " title = :title, updated_at = now() WHERE id = :t"
            ),
            {"n": turn_number, "reserve": reserve, "title": title, "t": thread_id},
        )
        return Reservation(
            thread_id,
            turn_id,
            request_id,
            model,
            turn_number,
            account_id,
            tuple(messages),
            reserve,
            registry.limits.max_spend_usd_per_thread,
        )


async def complete_turn(
    tenant_id: UUID,
    reservation: Reservation,
    assistant_text: str,
    usage: ChatUsage,
) -> Decimal:
    """Commit provider usage and release the conservative reservation atomically."""
    cost = actual_cost(reservation.model, usage)
    async with tenant_txn(tenant_id) as session:
        result = await session.execute(
            text(
                "UPDATE chat_turns SET assistant_content = :answer, status = 'completed',"
                " input_tokens = :input, output_tokens = :output, cache_read_tokens = :read,"
                " cache_write_tokens = :write, cost_usd = :cost, completed_at = now()"
                " WHERE id = :id AND thread_id = :thread AND status = 'reserved' RETURNING id"
            ),
            {
                "answer": assistant_text,
                "input": usage.input_tokens,
                "output": usage.output_tokens,
                "read": usage.cache_read_tokens,
                "write": usage.cache_write_tokens,
                "cost": cost,
                "id": reservation.turn_id,
                "thread": reservation.thread_id,
            },
        )
        if result.scalar_one_or_none() is None:
            raise ThreadBusy("turn is no longer reserved")
        updated = await session.execute(
            text(
                "UPDATE chat_threads SET reserved_usd = reserved_usd - :reserve,"
                " spent_usd = spent_usd + :cost, updated_at = now()"
                " WHERE id = :thread AND reserved_usd >= :reserve"
                " AND spent_usd + reserved_usd - :reserve + :cost <= :limit RETURNING id"
            ),
            {
                "reserve": reservation.estimated_cost_usd,
                "cost": cost,
                "limit": reservation.spend_limit_usd,
                "thread": reservation.thread_id,
            },
        )
        if updated.scalar_one_or_none() is None:
            raise ThreadBusy("thread cost reservation is inconsistent")
    _log.info(
        "chat.turn.completed",
        extra={
            "event": "chat.turn.completed",
            "tenant_id": str(tenant_id),
            "account_id": None if reservation.account_id is None else str(reservation.account_id),
            "client": "web",
            "phase": "chat",
            "model": reservation.model.id,
            "outcome": "success",
            "usage_known": True,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "cost_usd": str(cost),
        },
    )
    return cost


async def fail_turn(
    tenant_id: UUID, reservation: Reservation, *, charge_estimate: bool = True
) -> None:
    """Release a failed stream's reserve, charging the maximum only after a provider attempt."""
    async with tenant_txn(tenant_id) as session:
        charged = reservation.estimated_cost_usd if charge_estimate else Decimal(0)
        changed = await session.execute(
            text(
                "UPDATE chat_turns SET status = 'failed', cost_usd = :cost,"
                " completed_at = now() WHERE id = :id AND status = 'reserved' RETURNING id"
            ),
            {"id": reservation.turn_id, "cost": charged},
        )
        if changed.scalar_one_or_none() is None:
            return
        released = await session.execute(
            text(
                "UPDATE chat_threads SET reserved_usd = reserved_usd - :reserve,"
                " spent_usd = spent_usd + :cost, updated_at = now()"
                " WHERE id = :thread AND reserved_usd >= :reserve RETURNING id"
            ),
            {
                "reserve": reservation.estimated_cost_usd,
                "cost": charged,
                "thread": reservation.thread_id,
            },
        )
        if released.scalar_one_or_none() is None:
            raise ThreadBusy("thread cost reservation is inconsistent")
    _log.info(
        "chat.turn.failed",
        extra={
            "event": "chat.turn.failed",
            "tenant_id": str(tenant_id),
            "account_id": None if reservation.account_id is None else str(reservation.account_id),
            "client": "web",
            "phase": "chat",
            "model": reservation.model.id,
            "outcome": "failed",
            "usage_known": False,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": str(charged),
        },
    )


def _title(value: str) -> str:
    """Compact the first user turn for the sidebar without retaining control characters."""
    return re.sub(r"\s+", " ", value).strip()[:160]


# ---- from src/account_intelligence/chat/credentials.py ----


@dataclass(frozen=True)
class TenantKeyStatus:
    """Non-secret metadata returned by key management endpoints."""

    configured: bool
    updated_at: str | None


async def key_status(tenant_id: UUID) -> TenantKeyStatus:
    """Read non-secret status for the current tenant."""
    async with tenant_txn(tenant_id) as session:
        row = (
            await session.execute(
                text("SELECT updated_at FROM tenant_model_credentials WHERE provider = 'anthropic'")
            )
        ).first()
    return TenantKeyStatus(
        configured=row is not None,
        updated_at=None if row is None else row.updated_at.isoformat(),
    )


async def encrypted_key_for_thread(tenant_id: UUID, user_id: str, thread_id: UUID) -> bytes | None:
    """Read key ciphertext only after confirming the thread belongs to this member."""
    async with tenant_txn(tenant_id) as session:
        owner = (
            await session.execute(
                text("SELECT 1 FROM chat_threads WHERE id = :t AND owner_user_id = :u"),
                {"t": thread_id, "u": user_id},
            )
        ).first()
        if owner is None:
            return None
        row = await _encrypted_key(session)
    return None if row is None else bytes(row.ciphertext)


async def save_encrypted_key(session: AsyncSession, ciphertext: bytes, updated_by: str) -> str:
    """Upsert ciphertext only; caller includes the admin audit event in this transaction."""
    result = await session.execute(
        text(
            "INSERT INTO tenant_model_credentials (provider, ciphertext, updated_by)"
            " VALUES ('anthropic', :ciphertext, :actor)"
            " ON CONFLICT (tenant_id, provider) DO UPDATE SET"
            " ciphertext = EXCLUDED.ciphertext, updated_by = EXCLUDED.updated_by,"
            " updated_at = now() RETURNING updated_at"
        ),
        {"ciphertext": ciphertext, "actor": updated_by},
    )
    updated_at = cast(datetime, result.scalar_one())
    return updated_at.isoformat()


async def delete_encrypted_key(session: AsyncSession) -> bool:
    """Remove the tenant's stored ciphertext; caller audits atomically."""
    result = await session.execute(
        text("DELETE FROM tenant_model_credentials WHERE provider = 'anthropic' RETURNING provider")
    )
    return result.scalar_one_or_none() is not None
