"""Questions over one file: full-text retrieval over its stored units, then an answer that cites
them (``[C1]``...). Every citation names the unit's location (rows, page, slide, section,
member). A citation the model invents is dropped. Without a model the answer is extractive: the
most relevant passages, cited. The model call is estimated and gated like every model step.

GUARANTEE(test_answers_cite_only_retrieved_chunks)
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from uuid import UUID

from research_files import repo
from research_files.checks import redact_secrets
from research_files.modelsvc import FileModels, Item, ModelStop, parse_json_reply

PASSAGES = 6
PASSAGE_CHARS = 3000
_CITE = re.compile(r"\[C(\d+)\]")


async def ask(
    models: FileModels, tenant_id: UUID, file_id: UUID, question: str, approve: bool
) -> dict[str, Any]:
    """See the module docstring."""
    async with models.txn(tenant_id) as s:
        row = await repo.get_file(s, file_id)
        if row is None:
            raise LookupError("file not found")
        hits = await repo.search(s, file_id, question, PASSAGES)
    tables = {t["table"]: t for t in ((row.analytics or {}).get("tables") or [])}
    passages = []
    for n, hit in enumerate(hits, start=1):
        location = dict(hit.location)
        body = str(hit.content)[:PASSAGE_CHARS]
        table = location.get("table") or location.get("sheet")
        if table in tables and hit.kind == "rows":
            names = [c["name"] for c in tables[table]["columns"]]
            body = "columns: " + ", ".join(names) + "\n" + body
        passages.append(
            {"id": f"C{n}", "seq": hit.seq, "location": location, "text": redact_secrets(body)}
        )
    citations = [{"id": p["id"], "location": p["location"], "seq": p["seq"]} for p in passages]
    if not passages:
        return {
            "answer": "Nothing in this file matches the question.",
            "citations": [],
            "source": "retrieval",
            "estimate": None,
            "needs_approval": False,
        }
    if not models.configured:
        return {
            "answer": "No language model is configured, so here are the passages that best match"
            " the question.",
            "citations": citations,
            "passages": [{"id": p["id"], "text": p["text"][:600]} for p in passages],
            "source": "extractive",
            "estimate": None,
            "needs_approval": False,
        }
    digest = hashlib.sha256(
        (question.strip().lower() + "|" + "|".join(str(h.content_hash) for h in hits)).encode()
    ).hexdigest()
    item = Item(
        "answer_question",
        digest,
        {
            "question": question[:2000],
            "passages": [{"id": p["id"], "text": p["text"]} for p in passages],
        },
    )
    estimate = await models.estimate(tenant_id, [item], approve)
    if estimate.needs_approval:
        return {
            "answer": None,
            "citations": [],
            "source": "model",
            "estimate": estimate.to_json(),
            "needs_approval": True,
        }
    try:
        reply, served = await models.call(tenant_id, file_id, item)
    except ModelStop as stopped:
        return {
            "answer": None,
            "citations": citations,
            "source": "model",
            "error": stopped.reason,
            "estimate": estimate.to_json(),
            "needs_approval": False,
        }
    parsed = parse_json_reply(reply) or {"answer": reply}
    answer = str(parsed.get("answer") or "")
    known = {p["id"] for p in passages}
    cited = {f"C{m}" for m in _CITE.findall(answer)} | {
        str(c) for c in parsed.get("citations") or [] if isinstance(c, str)
    }
    answer = _CITE.sub(lambda m: m.group() if f"C{m.group(1)}" in known else "", answer)
    return {
        "answer": answer.strip(),
        "citations": [c for c in citations if c["id"] in cited & known],
        "source": "model",
        "served_from": served,
        "estimate": estimate.to_json(),
        "needs_approval": False,
    }
