"""Reference answers for the models that need them (Ask redesign 3c, 2026-11-02).

Claude Fable 5.1 answers a table question completely and in a steady shape; Sonnet and Haiku, left
to themselves, do not. The owner's aim is that their answers match Fable's, and a model cannot be
trained from here, so the match is shown instead: for a question like ones the reference model
already answered for THIS user, the earlier answer's shape is placed in front of the model
as a worked example — the headline, the numbers it led with, each table's columns and row count
with its first rows, the note and the callout. The model matches the completeness and structure,
not the wording, and takes every number from this turn's own tool results.

Where from: the user's own completed Ask turns written by a reference model
(``config/models.yaml`` ``reference_answer_models``), read under the user's own row-level security
and an explicit owner join — never another user's or another client's. Similarity is the overlap
of the question words; nothing below ``FLOOR`` is shown, so a question with no precedent gets no
example. No model call, nothing is logged but the count.

GUARANTEE(test_reference_answers_come_from_the_users_own_reference_turns)
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

SCAN = 200  # the most recent reference turns considered
FLOOR = 0.3  # the smallest question-word overlap that counts as a similar question
LIMIT = 2
ROWS_SHOWN = 3
CELL_CHARS = 60
EXAMPLE_CHARS = 1800
_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "on",
        "for",
        "and",
        "or",
        "is",
        "are",
        "was",
        "were",
        "be",
        "by",
        "with",
        "from",
        "at",
        "as",
        "that",
        "this",
        "these",
        "those",
        "it",
        "its",
        "who",
        "which",
        "what",
        "how",
        "many",
        "much",
        "all",
        "any",
        "me",
        "my",
        "our",
        "we",
        "you",
        "show",
        "list",
        "give",
        "tell",
        "find",
        "get",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "can",
        "could",
        "would",
        "should",
        "please",
    ]
)


def words(value: str) -> frozenset[str]:
    """The content words of a question: lower case, three letters or more, stop words out."""
    return frozenset(w for w in re.findall(r"[a-z0-9]{3,}", value.casefold()) if w not in _STOP)


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """The overlap of two word sets, 0 to 1 (shared words over all words)."""
    return len(a & b) / len(a | b) if a and b else 0.0


@dataclass(frozen=True)
class Reference:
    """One earlier answer by a reference model, and how close its question was."""

    question: str
    answer: Mapping[str, Any]
    score: float


def _cut(value: Any) -> str:
    # an earlier question or cell goes inside tags and quotes: neither may close them
    plain = " ".join(str(value).replace('"', "'").replace("<", "(").replace(">", ")").split())
    return plain if len(plain) <= CELL_CHARS else plain[: CELL_CHARS - 1].rstrip() + "…"


def has_table(answer: Mapping[str, Any]) -> bool:
    """Whether a stored structured answer carries a table."""
    return any(
        isinstance(s, Mapping) and s.get("kind") == "table" and s.get("rows")
        for s in answer.get("sections", ())
    )


async def reference_answers(
    session: Any,
    user_id: str,
    question: str,
    models: Collection[str],
    *,
    accounts: Collection[Any] | None = None,
    limit: int = LIMIT,
) -> list[Reference]:
    """The user's own most similar earlier answers by the reference ``models`` (see the module
    docstring), best first. ``accounts``: only turns asked about one of these accounts, or about
    no account in particular (an answer about account A is no example for a question about B).
    The questions are scored first; only the answers worth showing are read."""
    if not models:
        return []
    asked = words(question)
    if not asked:
        return []
    rows = (
        await session.execute(
            text(
                "SELECT t.id, t.user_content FROM chat_turns t JOIN chat_threads th"
                " ON th.id = t.thread_id AND th.owner_user_id = :u WHERE t.surface = 'ask'"
                " AND t.status = 'completed' AND t.model_id = ANY(:m)"
                " AND jsonb_typeof(t.answer_json->'structured') = 'object'"
                " AND (CAST(:a AS uuid[]) IS NULL OR t.account_id IS NULL"
                " OR t.account_id = ANY(CAST(:a AS uuid[])))"
                " ORDER BY t.created_at DESC LIMIT :n"
            ),
            {
                "u": user_id,
                "m": list(models),
                "n": SCAN,
                "a": None if accounts is None else [str(a) for a in accounts],
            },
        )
    ).all()
    scored = sorted(
        ((similarity(asked, words(str(r.user_content))), i, r) for i, r in enumerate(rows)),
        key=lambda x: (-x[0], x[1]),  # best first; equal scores keep the newest first
    )
    found: list[Reference] = []
    for score, _, r in scored:
        if score < FLOOR or len(found) >= limit:
            break
        stored = (
            await session.execute(
                text(
                    "SELECT answer_json->'structured' AS structured FROM chat_turns WHERE id = :i"
                ),
                {"i": r.id},
            )
        ).scalar_one()
        answer = json.loads(stored) if isinstance(stored, str) else stored
        if isinstance(answer, Mapping) and has_table(answer):
            found.append(Reference(str(r.user_content), answer, score))
    return found


def _example(ref: Reference) -> str:
    a = ref.answer
    lines = [f'<example question="{_cut(ref.question)}">', f"headline: {_cut(a.get('headline'))}"]
    if a.get("stats"):
        lines.append(
            "numbers: " + "; ".join(f"{_cut(s['label'])} = {_cut(s['value'])}" for s in a["stats"])
        )
    for s in a.get("sections", ()):
        if not isinstance(s, Mapping):
            continue
        if s.get("kind") == "table" and s.get("rows"):
            rows = s["rows"]
            title = f' "{_cut(s["title"])}"' if s.get("title") else ""
            lines.append(
                f"table{title}: {len(rows)} rows; columns: {' | '.join(map(_cut, s['columns']))}"
            )
            lines += [f"  e.g. {' | '.join(map(_cut, r))}" for r in rows[:ROWS_SHOWN]]
            if s.get("note"):
                lines.append(f"  note: {_cut(s['note'])}")
        elif s.get("kind") == "callout":
            lines.append(f"callout: {_cut(s.get('text'))}")
    lines.append("</example>")
    return "\n".join(lines)[:EXAMPLE_CHARS]


def render(refs: Sequence[Reference]) -> str:
    """The examples as the prompt carries them, or "" for none."""
    if not refs:
        return ""
    return (
        "<reference_answers>\nAnswers the reference model gave THIS user to similar questions"
        " before. Match their completeness and structure — what the table lists, how counts and"
        " their bases are stated, what the callout flags — not their wording. The data may have"
        " changed since: take every number and row from this turn's tool results.\n"
        + "\n".join(_example(r) for r in refs)
        + "\n</reference_answers>"
    )
