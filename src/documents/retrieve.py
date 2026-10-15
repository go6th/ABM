"""Hybrid retrieval over the client's own uploaded documents for Ask (audit 2026-10-15): Ask
used only the facts extracted into eight sales categories and never read the passages, so a
question about anything else in a file found nothing.

* Lexical: full-text search on ``document_passages.tsv`` (``simple`` config, GIN index), ranked by
  ``ts_rank_cd``.
* Dense: cosine distance on ``chunks.semantic_embedding`` (an on-server transformer, owner
  decision 2026-10-15) with the question embedded by the same model; skipped without one.
  Chunks indexed before it existed get their vector here, ``BACKFILL_LIMIT`` per question.
* Fusion: reciprocal rank fusion of the two rankings (``rag.retrieval``), then the top ``k``.

Only the accounts in ``scope`` (the caller's own, already owner-checked by row level security)
are searched. Passages are data for the model, never instructions; they stay private to the
workspace like every other "From your files" row.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.rag.retrieval import reciprocal_rank_fusion
from documents import repo

LEXICAL_LIMIT = 30
BACKFILL_LIMIT = 200
DENSE_LIMIT = 30
TOP_K = 10
MAX_PASSAGE_CHARS = 1500


@dataclass(frozen=True)
class Passage:
    """One retrieved passage and where it came from."""

    key: str
    account_id: UUID
    document: str
    content: str
    cite: str | None
    score: float


async def _lexical(session: Any, question: str, scope: list[UUID]) -> list[dict[str, Any]]:
    rows = await session.execute(
        text(
            "SELECT p.document_id::text || ':' || p.seq AS key, p.account_id, p.content,"
            " p.location, coalesce(d.name, 'an uploaded file') AS document"
            " FROM document_passages p JOIN documents d ON d.id = p.document_id"
            " WHERE p.account_id = ANY(:scope)"
            " AND p.tsv @@ websearch_to_tsquery('simple', :q)"
            " ORDER BY ts_rank_cd(p.tsv, websearch_to_tsquery('simple', :q)) DESC LIMIT :n"
        ),
        {"scope": scope, "q": question, "n": LEXICAL_LIMIT},
    )
    return [dict(r._mapping) for r in rows]


async def _dense(
    session: Any, vector: Sequence[float], scope: list[UUID], model: str
) -> list[dict[str, Any]]:
    from documents.embed import literal

    rows = await session.execute(
        text(
            "SELECT c.doc_id::text || ':' || c.passage_seq AS key, c.account_id, c.content,"
            " c.location, coalesce(d.name, 'an uploaded file') AS document"
            " FROM chunks c JOIN documents d ON d.id = c.doc_id"
            " WHERE c.account_id = ANY(:scope) AND c.semantic_model = :m"
            " ORDER BY c.semantic_embedding <=> CAST(:v AS halfvec) LIMIT :n"
        ),
        {"scope": scope, "v": literal(vector), "m": model, "n": DENSE_LIMIT},
    )
    return [dict(r._mapping) for r in rows]


async def backfill(session: Any, scope: list[UUID], embedder: Any) -> int:
    """Give up to ``BACKFILL_LIMIT`` chunks of these accounts that predate the retrieval vectors
    their vector (bounded, so a question never waits long); returns how many were filled."""
    from documents.embed import literal

    missing = (
        await session.execute(
            text(
                "SELECT tenant_id, id, content FROM chunks WHERE account_id = ANY(:scope)"
                " AND (semantic_model IS DISTINCT FROM :m) LIMIT :n"
            ),
            {"scope": scope, "m": str(embedder.model), "n": BACKFILL_LIMIT},
        )
    ).all()
    if not missing:
        return 0
    vectors = await embedder.embed([str(r.content) for r in missing])
    await repo.set_semantic_vectors(
        session,
        [(r.tenant_id, r.id, literal(v)) for r, v in zip(missing, vectors, strict=True)],
        str(embedder.model),
    )
    return len(missing)


def _cite(location: Any) -> str | None:
    if not isinstance(location, dict):
        return None
    try:
        from documents.chunking import cite

        return str(cite(location)) or None
    except Exception:  # noqa: BLE001 - a location without a citation still answers
        return None


async def retrieve(
    session: Any,
    question: str,
    scope: Sequence[UUID],
    embedder: Any | None = None,
    k: int = TOP_K,
) -> list[Passage]:
    """The ``k`` passages of the caller's documents that best answer ``question``."""
    accounts = list(scope)
    if not accounts or not question.strip():
        return []
    lexical = await _lexical(session, question, accounts)
    dense: list[dict[str, Any]] = []
    if embedder is not None:
        try:
            # its own savepoint: a failure here never spoils the keyword results or the answer
            async with session.begin_nested():
                await backfill(session, accounts, embedder)
                embed_query = getattr(embedder, "embed_query", embedder.embed)
                (vector,) = await embed_query([question])
                dense = await _dense(session, vector, accounts, str(embedder.model))
        except Exception:  # noqa: BLE001 - keyword search still answers
            dense = []
    rows = {r["key"]: r for r in dense}
    rows.update({r["key"]: r for r in lexical})
    fused = reciprocal_rank_fusion([[r["key"] for r in lexical], [r["key"] for r in dense]])
    best = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:k]
    return [
        Passage(
            key=key,
            account_id=UUID(str(rows[key]["account_id"])),
            document=str(rows[key]["document"]),
            content=str(rows[key]["content"])[:MAX_PASSAGE_CHARS],
            cite=_cite(rows[key]["location"]),
            score=score,
        )
        for key, score in best
        if rows[key]["account_id"] is not None
    ]
