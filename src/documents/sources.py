"""The prose-side access tools (the Ask harness, 2026-10-17): ``list_sources``,
``search_passages``, ``read_passage`` and ``read_span`` over the caller's own uploaded files,
so the model decides the access path instead of answering from a top-k sample alone.

* ``list_sources()``: the workspace's members with their shape (``tabular`` when the member
  holds table rows, ``transcript`` when it holds transcript windows, else ``prose``), row and
  page counts, the file's sha256 and when it was ingested. The model reads this first on a
  question it cannot place.
* ``search_passages(query, filters, top_k)``: the hybrid (keyword + vector, reciprocal rank
  fusion) search ``documents.retrieve`` already runs for Ask, now on demand, with ``top_k`` up
  to 50; filters restrict the search to a document, member or passage kind and nothing else
  (fail closed: an unknown filter is a structured error).
* ``read_passage(chunk_id)``: the exact stored text of one passage the search cited
  (``<document id>:<seq>``), never re-computed.
* ``read_span(member, from_chunk, to_chunk)``: the exact text of every passage from one cited
  chunk to the next (inclusive), so the model can widen its context around a citation without
  a new search; bounded at ``READ_SPAN_MAX_CHARS``.

Everything is tenant-scoped by the caller's transaction (row level security) plus the explicit
account scope, errors are structured (``documents.tables.QueryError``), and passage text is the
already-redacted stored text: the tools return nothing the file view does not hold.

GUARANTEE(test_the_prose_tools_search_and_read_cited_text)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import text as sql_text

from documents.tables import QueryError

SEARCH_TOP_K_MAX = 50
READ_SPAN_MAX_CHARS = 24_000
FILTER_FIELDS = ("document", "member", "kind")

LIST_SOURCES_TOOL: dict[str, Any] = {
    "name": "list_sources",
    "description": (
        "The workspace's files and their members, each with its shape (tabular: query it with"
        " describe_table/query_table; transcript/prose: search its passages), row or page"
        " counts, sha256 and ingest time. Call it first when you cannot place what the question"
        " is about."
    ),
    "input_schema": {"type": "object", "additionalProperties": False, "properties": {}},
}
SEARCH_PASSAGES_TOOL: dict[str, Any] = {
    "name": "search_passages",
    "description": (
        "Hybrid keyword+vector search over the workspace's passages (prose text and the slim"
        " row passages of tables). Returns ranked passages with the chunk id read_passage reads"
        " and the citation of each. Use for prose questions and to find wording; use"
        " query_table for filters, counts and lookups."
    ),
    "input_schema": {
        "type": "object",
        "required": ["query"],
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string"},
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["field", "op", "value"],
                    "additionalProperties": False,
                    "properties": {
                        "field": {"enum": list(FILTER_FIELDS)},
                        "op": {"enum": ["eq", "icontains"]},
                        "value": {"type": "string"},
                    },
                },
            },
            "top_k": {"type": "integer", "minimum": 1, "maximum": SEARCH_TOP_K_MAX},
        },
    },
}
READ_PASSAGE_TOOL: dict[str, Any] = {
    "name": "read_passage",
    "description": (
        "The exact stored text of one passage by its chunk id (from search_passages), with its"
        " citation. Use to quote precisely instead of paraphrasing a search snippet."
    ),
    "input_schema": {
        "type": "object",
        "required": ["chunk_id"],
        "additionalProperties": False,
        "properties": {"chunk_id": {"type": "string"}},
    },
}
READ_SPAN_TOOL: dict[str, Any] = {
    "name": "read_span",
    "description": (
        "The exact text of every passage from one cited chunk to another of the same member,"
        " so you can widen the context around a citation without a new search. One call, up to"
        f" {READ_SPAN_MAX_CHARS} characters; a longer span is cut at the boundary and flagged"
        " truncated."
    ),
    "input_schema": {
        "type": "object",
        "required": ["from_chunk", "to_chunk"],
        "additionalProperties": False,
        "properties": {
            "from_chunk": {"type": "string"},
            "to_chunk": {"type": "string"},
        },
    },
}
SOURCE_TOOLS: tuple[dict[str, Any], ...] = (
    LIST_SOURCES_TOOL,
    SEARCH_PASSAGES_TOOL,
    READ_PASSAGE_TOOL,
    READ_SPAN_TOOL,
)


def parse_chunk_id(value: Any) -> tuple[UUID, int]:
    """A chunk id ``<document uuid>:<passage seq>`` as ``search_passages`` returns it."""
    raw = str(value or "").strip()
    head, _, tail = raw.rpartition(":")
    try:
        return UUID(head), int(tail)
    except (ValueError, AttributeError, ArithmeticError) as error:
        raise QueryError(
            "bad_chunk_id", f"no chunk id {raw!r}; a chunk id is <document>:<seq>"
        ) from error


async def list_sources(session: Any, accounts: Sequence[UUID]) -> dict[str, Any]:
    """The members of these accounts' indexed documents with their shape and counts (see the
    module docstring). A member's shape comes from what it stored: table rows (``tabular``),
    transcript windows or prose."""
    rows = await session.execute(
        sql_text(
            "SELECT d.id AS document_id, d.name AS document, d.sha256, d.created_at,"
            " d.detected_type, p.location->>'member' AS member,"
            " bool_or(p.kind = 'crm_row') AS has_rows,"
            " bool_or(p.kind = 'transcript_window') AS has_transcript,"
            " count(*) AS passages,"
            " count(DISTINCT p.location->>'row') FILTER (WHERE p.kind = 'crm_row') AS rows,"
            " max((p.location->>'page')::int) FILTER (WHERE p.kind <> 'crm_row') AS pages"
            " FROM document_passages p JOIN documents d ON d.id = p.document_id"
            " WHERE d.account_id = ANY(:scope) AND d.status = 'indexed'"
            " GROUP BY d.id, d.name, d.sha256, d.created_at, d.detected_type, 6"
            " ORDER BY d.name, member NULLS FIRST"
        ),
        {"scope": list(accounts)},
    )
    members = []
    for r in rows:
        shape = (
            "tabular" if r.has_rows else "transcript" if r.has_transcript else "prose"  # fmt: skip
        )
        members.append(
            {
                "document": str(r.document),
                "document_id": str(r.document_id),
                "member": str(r.member) if r.member else None,
                "shape": shape,
                "passages": int(r.passages),
                "rows": int(r.rows) if r.has_rows else None,
                "pages": int(r.pages) if r.pages else None,
                "sha256": str(r.sha256) if r.sha256 else None,
                "ingested_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return {"sources": members, "count": len(members)}


def parse_filters(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    """Validated ``filters`` items (field, op, value); unknown fields are a structured error."""
    items = payload.get("filters") or []
    if not isinstance(items, list):
        raise QueryError("bad_filters", "filters must be a list of {field, op, value}")
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise QueryError("bad_filters", "each filter needs field, op and value")
        field, op, value = item.get("field"), item.get("op"), item.get("value")
        if field not in FILTER_FIELDS:
            raise QueryError(
                "unknown_filter",
                f"filters are on {', '.join(FILTER_FIELDS)} only, not {field!r}",
                fields=FILTER_FIELDS,
            )
        if op not in ("eq", "icontains") or value is None:
            raise QueryError("bad_filters", "a filter op is eq or icontains and needs a value")
        out.append({"field": str(field), "op": str(op), "value": str(value)})
    return out


async def search(
    session: Any,
    question: str,
    scope: Sequence[UUID],
    embedder: Any | None,
    top_k: int,
    filters: Sequence[dict[str, str]],
) -> dict[str, Any]:
    """The ``top_k`` passages the hybrid search ranks first for ``question``, narrowed by the
    validated filters; each with its chunk id and citation."""
    from documents.retrieve import retrieve

    # a document filter narrows the retrieval itself (within=), not the results after it, so a
    # filtered search finds passages of the named file however small its share of the workspace
    fetch = min(top_k, SEARCH_TOP_K_MAX) if not filters else SEARCH_TOP_K_MAX
    for f in filters:  # fail closed at the store too, not only at the tool wrapper
        if f["field"] not in FILTER_FIELDS:
            raise QueryError(
                "unknown_filter",
                f"filters are on {', '.join(FILTER_FIELDS)} only, not {f['field']!r}",
            )
    doc_ids: set[str] | None = None
    narrowed = [f for f in filters if f["field"] != "document"]
    with_docs = [f for f in filters if f["field"] == "document"]
    if with_docs:
        listed = await list_sources(session, scope)
        for f in with_docs:
            wanted = f["value"].casefold()
            match = {
                m["document_id"]
                for m in listed["sources"]
                if (m["document"].casefold() == wanted if f["op"] == "eq"
                    else wanted in m["document"].casefold())
            }  # fmt: skip
            doc_ids = match if doc_ids is None else doc_ids & match
        if not doc_ids:
            return {"passages": [], "hits": 0}
    within = [UUID(d) for d in doc_ids] if doc_ids is not None else None
    passages = await retrieve(session, question, scope, embedder, k=fetch, within=within)
    out: list[tuple[Any, Any]] = []
    for p in passages:
        doc_id, seq = parse_chunk_id(p.key)
        meta = (
            await session.execute(
                sql_text(
                    "SELECT p.location, p.kind FROM document_passages p"
                    " WHERE p.document_id = :d AND p.seq = :s"
                ),
                {"d": doc_id, "s": seq},
            )
        ).first()
        out.append((p, meta))
    kept = []
    for p, meta in out:
        if doc_ids is not None and str(p.key).split(":")[0] not in doc_ids:
            continue
        ok = True
        for f in narrowed:
            value = (
                str(getattr(meta, "location", {}) .get("member") or "") if f["field"] == "member"
                else str(getattr(meta, "kind", "") or "")
            )  # fmt: skip
            if (f["op"] == "eq" and value.casefold() != f["value"].casefold()) or (
                f["op"] == "icontains" and f["value"].casefold() not in value.casefold()
            ):
                ok = False
        if ok:
            kept.append(
                {
                    "chunk_id": p.key,
                    "text": p.content,
                    "document": p.document,
                    "location": meta.location if meta else None,
                    "kind": meta.kind if meta else None,
                    "cite": p.cite,
                    "score": round(p.score, 6),
                }
            )
    return {"passages": kept[:top_k], "hits": len(kept)}


async def read_passage(session: Any, chunk_id: str, scope: Sequence[UUID]) -> dict[str, Any]:
    """The exact stored text of one chunk (see the module docstring); a chunk outside the
    caller's accounts is not found, never leaked."""
    document_id, seq = parse_chunk_id(chunk_id)
    row = (
        await session.execute(
            sql_text(
                "SELECT p.content, p.location, p.kind, coalesce(d.name, 'an uploaded file')"
                " AS document FROM document_passages p JOIN documents d ON d.id ="
                " p.document_id WHERE p.document_id = :d AND p.seq = :s AND p.account_id ="
                " ANY(:scope)"
            ),
            {"d": document_id, "s": seq, "scope": list(scope)},
        )
    ).first()
    if row is None:
        raise QueryError("not_found", f"no passage {chunk_id!r} in this workspace")
    from documents.chunking import cite

    return {
        "chunk_id": f"{document_id}:{seq}",
        "document": str(row.document),
        "kind": str(row.kind),
        "location": row.location,
        "cite": str(cite(row.location)) if row.location else None,
        "text": str(row.content),
    }


async def read_span(
    session: Any, from_chunk: str, to_chunk: str, scope: Sequence[UUID]
) -> dict[str, Any]:
    """The text of every passage from ``from_chunk`` to ``to_chunk`` (both of one member),
    in passage order; ``truncated`` when the span is longer than ``READ_SPAN_MAX_CHARS``."""
    first, last = parse_chunk_id(from_chunk), parse_chunk_id(to_chunk)
    if first[0] != last[0]:
        raise QueryError("bad_span", "a span stays inside one file: the chunk ids differ")
    if last[1] < first[1]:
        raise QueryError("bad_span", "the span's end is before its start")
    rows = (
        await session.execute(
            sql_text(
                "SELECT p.seq, p.content, p.location FROM document_passages p WHERE"
                " p.document_id = :d AND p.seq BETWEEN :a AND :b AND p.account_id = ANY(:scope)"
                " ORDER BY p.seq"
            ),
            {"d": first[0], "a": first[1], "b": last[1], "scope": list(scope)},
        )
    ).all()
    if not rows:
        raise QueryError("not_found", "no passage in that span of this workspace")
    total, parts = 0, []
    truncated = False
    for r in rows:
        piece = str(r.content)
        if total + len(piece) > READ_SPAN_MAX_CHARS:
            truncated = True
            break
        parts.append(piece)
        total += len(piece) + 2
    from documents.chunking import cite

    return {
        "document_id": str(first[0]),
        "from_chunk": f"{first[0]}:{first[1]}",
        "to_chunk": f"{last[0]}:{last[1]}",
        "passages": len(parts),
        "truncated": truncated,
        "cite": [str(cite(r.location)) for r in rows[: len(parts)] if r.location],
        "text": "\n\n".join(parts),
    }
