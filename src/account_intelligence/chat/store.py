"""RLS-scoped chat thread persistence with serialized turn reservations and cost accounting."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.chat.registry import ChatModel, ChatRegistry
from account_intelligence.chat.service import (
    ChatMessage,
    ChatService,
    ChatUsage,
    actual_cost,
)
from account_intelligence.db.session import tenant_txn
from obs.logging import logger

_log = logger(__name__)


class ThreadNotFound(LookupError):
    """Thread is absent, belongs to another member, or is hidden by tenant RLS."""


class ThreadLimitReached(RuntimeError):
    """A configured turn or spend limit prevents another provider request."""


class ThreadBusy(RuntimeError):
    """A turn is already streaming or the idempotency key belongs to another request."""


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


async def create_thread(tenant_id: UUID, user_id: str, *, txn: Any = tenant_txn) -> UUID:
    """Create an empty private conversation for the verified tenant member (``txn`` is a
    ``tenant_txn``-shaped factory; the route passes its audited one)."""
    async with txn(tenant_id) as session:
        value = await session.execute(
            text("INSERT INTO chat_threads (owner_user_id) VALUES (:u) RETURNING id"),
            {"u": user_id},
        )
        return UUID(str(value.scalar_one()))


async def list_threads(tenant_id: UUID, user_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Return only the caller's recent threads; tenant identity comes from the verified token."""
    async with tenant_txn(tenant_id) as session:
        rows = (
            await session.execute(
                text(
                    "SELECT t.id, t.title, t.turn_count, t.spent_usd, t.updated_at,"
                    " (SELECT model_id FROM chat_turns c WHERE c.thread_id = t.id"
                    " ORDER BY turn_number DESC LIMIT 1) AS model_id"
                    " FROM chat_threads t WHERE t.owner_user_id = :u"
                    " ORDER BY t.updated_at DESC, t.id DESC LIMIT :lim"
                ),
                {"u": user_id, "lim": limit},
            )
        ).all()
    return [
        {
            "id": str(row.id),
            "title": row.title,
            "turn_count": row.turn_count,
            "spent_usd": str(row.spent_usd),
            "updated_at": row.updated_at.isoformat(),
            "model_id": row.model_id,
        }
        for row in rows
    ]


async def get_thread(tenant_id: UUID, user_id: str, thread_id: UUID) -> dict[str, Any]:
    """Load one private conversation and its turns in a stable chronological order."""
    async with tenant_txn(tenant_id) as session:
        thread = (
            await session.execute(
                text(
                    "SELECT id, title, turn_count, spent_usd, reserved_usd, created_at, updated_at"
                    " FROM chat_threads WHERE id = :t AND owner_user_id = :u"
                ),
                {"t": thread_id, "u": user_id},
            )
        ).first()
        if thread is None:
            raise ThreadNotFound
        turns = (
            await session.execute(
                text(
                    "SELECT id, turn_number, request_id, model_id, user_content, assistant_content,"
                    " client, account_id, phase, status, input_tokens, output_tokens,"
                    " cache_read_tokens, cache_write_tokens, cost_usd, created_at, completed_at,"
                    " answer_json"
                    " FROM chat_turns WHERE thread_id = :t ORDER BY turn_number"
                ),
                {"t": thread_id},
            )
        ).all()
    return {
        "id": str(thread.id),
        "title": thread.title,
        "turn_count": thread.turn_count,
        "spent_usd": str(thread.spent_usd),
        "reserved_usd": str(thread.reserved_usd),
        "created_at": thread.created_at.isoformat(),
        "updated_at": thread.updated_at.isoformat(),
        "turns": [
            {
                "id": str(row.id),
                "turn_number": row.turn_number,
                "request_id": str(row.request_id),
                "model_id": row.model_id,
                "user_content": row.user_content,
                "assistant_content": row.assistant_content,
                "client": row.client,
                "account_id": None if row.account_id is None else str(row.account_id),
                "phase": row.phase,
                "status": row.status,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "cache_read_tokens": row.cache_read_tokens,
                "cache_write_tokens": row.cache_write_tokens,
                "cost_usd": str(row.cost_usd),
                "created_at": row.created_at.isoformat(),
                "completed_at": None if row.completed_at is None else row.completed_at.isoformat(),
                "answer": row.answer_json,
            }
            for row in turns
        ],
    }


async def delete_thread(tenant_id: UUID, user_id: str, thread_id: UUID) -> None:
    """Permanently delete a caller-owned thread and its cascade-deleted message history."""
    async with tenant_txn(tenant_id) as session:
        thread = (
            await session.execute(
                text("SELECT id FROM chat_threads WHERE id = :t AND owner_user_id = :u FOR UPDATE"),
                {"t": thread_id, "u": user_id},
            )
        ).first()
        if thread is None:
            raise ThreadNotFound
        active = (
            await session.execute(
                text(
                    "SELECT 1 FROM chat_threads t JOIN chat_turns c ON c.thread_id = t.id"
                    " WHERE t.id = :t AND t.owner_user_id = :u AND c.status = 'reserved' LIMIT 1"
                ),
                {"t": thread_id, "u": user_id},
            )
        ).first()
        if active is not None:
            raise ThreadBusy("a response is already in progress")
        await session.execute(
            text("DELETE FROM chat_threads WHERE id = :t AND owner_user_id = :u RETURNING id"),
            {"t": thread_id, "u": user_id},
        )


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
                    text("SELECT 1 FROM accounts WHERE id = :a"), {"a": account_id}
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


async def record_ask_turn(
    session: Any,
    *,
    user_id: str,
    thread_id: UUID | None,
    request_id: UUID,
    model_id: str,
    question: str,
    plain_answer: str,
    account_id: UUID | None,
    input_tokens: int,
    output_tokens: int,
    answer_json: str,
    week: Any,
    category: str,
) -> tuple[UUID, UUID, bool]:
    """Store one completed Ask turn in the caller's transaction: the thread (created on the first
    question), the turn with its structured answer, and the weekly question count for its
    category. Returns (thread id, turn id, whether the thread was created)."""
    created = False
    if thread_id is None:
        thread_id = UUID(
            str(
                (
                    await session.execute(
                        text(
                            "INSERT INTO chat_threads (owner_user_id, title) VALUES (:u, :t)"
                            " RETURNING id"
                        ),
                        {"u": user_id, "t": " ".join(question.split())[:80]},
                    )
                ).scalar_one()
            )
        )
        created = True
    number = (
        await session.execute(
            text(
                "UPDATE chat_threads SET turn_count = turn_count + 1, updated_at = now()"
                " WHERE id = :t AND owner_user_id = :u RETURNING turn_count"
            ),
            {"t": thread_id, "u": user_id},
        )
    ).scalar_one()
    turn_id = (
        await session.execute(
            text(
                "INSERT INTO chat_turns (thread_id, turn_number, request_id, model_id,"
                " user_content, assistant_content, account_id, status, reserved_usd,"
                " input_tokens, output_tokens, completed_at, answer_json) VALUES (:t, :n, :r,"
                " :m, :q, :a, :acct, 'completed', 0, :it, :ot, now(), CAST(:j AS jsonb))"
                " RETURNING id"
            ),
            {
                "t": thread_id,
                "n": number,
                "r": request_id,
                "m": model_id,
                "q": question,
                "a": plain_answer,
                "acct": account_id,
                "it": input_tokens,
                "ot": output_tokens,
                "j": answer_json,
            },
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO question_categories (week, category, questions) VALUES (:w, :c, 1)"
            " ON CONFLICT (tenant_id, week, category) DO UPDATE"
            " SET questions = question_categories.questions + 1"
        ),
        {"w": week, "c": category},
    )
    return thread_id, UUID(str(turn_id)), created
