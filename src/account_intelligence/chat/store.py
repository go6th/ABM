"""RLS-scoped chat thread persistence with serialized turn reservations and cost accounting."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.db.session import tenant_txn
from obs.logging import logger

_log = logger(__name__)


class ThreadNotFound(LookupError):
    """Thread is absent, belongs to another member, or is hidden by tenant RLS."""


class ThreadBusy(RuntimeError):
    """A turn is already streaming or the idempotency key belongs to another request."""


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


async def ask_history(
    session: Any, *, thread_id: UUID, limit: int, skip_model_id: str
) -> list[tuple[str, str]]:
    """The thread's last ``limit`` completed Ask turns as (question, plain answer), oldest first.
    Turns the workspace's own composer wrote (``skip_model_id``) are left out: a placeholder
    answer is no history. The caller has checked that the user owns the thread."""
    rows = (
        await session.execute(
            text(
                "SELECT user_content, assistant_content FROM chat_turns WHERE thread_id = :t"
                " AND surface = 'ask' AND status = 'completed' AND model_id <> :skip"
                " ORDER BY turn_number DESC LIMIT :n"
            ),
            {"t": thread_id, "skip": skip_model_id, "n": limit},
        )
    ).all()
    return [(str(r.user_content), str(r.assistant_content)) for r in reversed(rows)]


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
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    cost_usd: Decimal = Decimal(0),
    billed_model_id: str | None = None,
) -> tuple[UUID, UUID, bool]:
    """Store one completed Ask turn in the caller's transaction: the thread (created on the first
    question), the turn with its structured answer, and the weekly question count for its
    category. Returns (thread id, turn id, whether the thread was created). The turn carries
    every token class and its priced cost, on the model that was paid (``billed_model_id``:
    a reply the answer did not use was still paid; ``model_id`` names who wrote the answer),
    with ``surface`` 'ask' (the cost view, migration 0079)."""
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
                " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd,"
                " billed_model_id, surface, completed_at, answer_json) VALUES (:t, :n, :r,"
                " :m, :q, :a, :acct, 'completed', 0, :it, :ot, :cr, :cw, :cost, :billed,"
                " 'ask', now(), CAST(:j AS jsonb)) RETURNING id"
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
                "cr": cache_read_tokens,
                "cw": cache_write_tokens,
                "cost": cost_usd,
                "billed": billed_model_id,
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
