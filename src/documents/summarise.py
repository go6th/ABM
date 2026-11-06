"""The written summary of a file analysis (owner's rule 2026-11-03: a model reads every uploaded
file and the account page shows what it found, the Account Summary first of all).

After the facts of a file-mode run are extracted (``documents.extract``), one model call writes
the account page's one-minute version and its hypotheses, cautions and first-call questions from
those facts alone, and ``sections_of`` turns the reply into ``report_sections`` rows (the same
rows the research's own narrative lives in, written through ``agents.production.state``). The
reply is JSON validated against ``SummaryReply``; a reply that does not fit is asked for once
more, then dropped (the run completes without a narrative and counts it).

The model sees facts the platform already checked in code (each one quotes its passage), never
the raw passages: the untrusted-content boundary stays where the extractor put it.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from llm.schema import REASK_NOTE, validate

MAX_FACTS = 200
MAX_PROMPT_CHARS = 60_000

SYSTEM = """You write the one-minute account summary of a sales account from facts that were read
in the client's own uploaded files. Every fact below was checked against its file; use nothing
else and invent nothing. Answer with JSON only, in this shape:
{"narrative": "three to six plain sentences on what the files say about the account",
 "hypotheses": ["what the facts suggest but do not state, each one sentence"],
 "cautions": ["what a seller should be careful about, each one sentence"],
 "first_call_questions": ["a question to confirm a hypothesis on a first call"]}
Keep every list to at most eight items; use an empty list when the facts give nothing. Name
people only as the facts name them. Do not mention tools, vendors or data sources."""

ModelCall = Callable[[str, str, str], Awaitable[Any]]


class SummaryReply(BaseModel):
    """The model's summary, validated before anything is stored."""

    model_config = ConfigDict(extra="forbid")
    narrative: str = Field(min_length=1, max_length=4000)
    hypotheses: list[str] = Field(default_factory=list, max_length=8)
    cautions: list[str] = Field(default_factory=list, max_length=8)
    first_call_questions: list[str] = Field(default_factory=list, max_length=8)


def user_prompt(company: str, findings: Sequence[Any]) -> str:
    """The facts as JSON (category, value, grade, citation), capped in count and size."""
    facts = []
    size = 0
    for f in findings[:MAX_FACTS]:
        item = {
            "category": str(getattr(f, "category", "")),
            "value": str(getattr(f, "value", ""))[:500],
            "grade": str(getattr(f, "grade", "")),
            "citation": str(getattr(f, "citation", ""))[:200],
        }
        size += len(json.dumps(item))
        if size > MAX_PROMPT_CHARS:
            break
        facts.append(item)
    return json.dumps({"kind": "file_summary", "company": company, "facts": facts})


async def summarise(model: ModelCall, company: str, findings: Sequence[Any]) -> SummaryReply | None:
    """The validated summary, or None when the model's reply did not fit twice."""
    prompt = user_prompt(company, findings)
    # LOOP(file summary schema re-ask): cap=2 attempts; exit=valid reply, dropped summary
    for attempt in range(2):
        result = await model("file_summarise", SYSTEM, prompt + (REASK_NOTE if attempt else ""))
        reply = validate(str(getattr(result, "text", result)), SummaryReply)
        if reply is not None:
            return reply
    return None


def sections_of(reply: SummaryReply) -> list[dict[str, Any]]:
    """The reply as ``report_sections`` rows: the narrative first, then each list in order."""
    out: list[dict[str, Any]] = [{"kind": "narrative", "text": reply.narrative}]
    for kind, items in (
        ("hypothesis", reply.hypotheses),
        ("caution", reply.cautions),
        ("first_call_question", reply.first_call_questions),
    ):
        out.extend({"kind": kind, "text": " ".join(t.split())} for t in items if t.strip())
    return out
