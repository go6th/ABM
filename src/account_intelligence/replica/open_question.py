"""Open questions: the questions Ask answers as a few seller conclusions, not as a table.

"What are the top three budget insights?" asks for judgment over the whole workspace, so its
answer is N insights (each a conclusion, one grade, one next action), built from the Account
Summary's signal tables first. A list, count or lookup ("how many postings", "show me the
Salesforce listings", "who is in the Bay Area") keeps the every-matching-row table. The decision
is a fixed word rule in code, so every model gets the same answer shape (Sonnet deep-thinking
plan and Ask insight-answers plan, 2026-11-03).

``open_question(text)`` returns the shape, or None for a list question:

* ``count`` is the number of insights the question asks for ("top three", "5 key takeaways"),
  clamped to ``MAX_INSIGHTS``; ``asked`` says whether the question named it (then the answer
  carries exactly that many), else the answer carries 1 to ``MAX_INSIGHTS`` and ``count`` is
  the default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_INSIGHTS = 3
MAX_INSIGHTS = 5

# words that ask for judgment over the workspace
_OPEN = re.compile(
    r"\b("
    r"insights?|takeaways?|priorit(?:y|ies|ise|ize)|what matters|matter most|most important"
    r"|budgets?|spend(?:ing)?|opportunit(?:y|ies)|angles?|summar(?:y|ise|ize)|overview"
    r"|big picture|story|themes?|implications?|so what|why should|what should (?:i|we)"
    r"|how should (?:i|we)|where should (?:i|we)|recommend(?:ation)?s?|strateg(?:y|ic)"
    r"|biggest|key signals?|stand(?:s)? out"
    r")\b",
    re.IGNORECASE,
)
# words that ask for rows: a list question even when an open word appears ("list the budget
# owners", "how many postings mention budget")
_LIST = re.compile(
    r"\b("
    r"how many|count|number of|list(?:ing)?s?|show me|give me (?:all|the list|every)"
    r"|every (?:posting|person|people|job|row)|all (?:the )?(?:postings|people|jobs|rows|contacts)"
    r"|which (?:people|persons|postings|jobs|contacts)|who (?:is|are|works)"
    r"|linkedin urls?|urls?|links?|emails?|table of"
    r")\b",
    re.IGNORECASE,
)
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_COUNT = re.compile(
    r"\b(?:top|key|main|biggest|first)?\s*(\d{1,2}|one|two|three|four|five|six|seven|eight|nine"
    r"|ten)\s+(?:\w+\s+){0,3}?(?:insights?|takeaways?|priorit(?:y|ies)|points?|things|signals?"
    r"|reasons?|opportunit(?:y|ies)|themes?|angles?|implications?|recommendations?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OpenQuestion:
    """The shape of an open question's answer: how many insights, and whether the question
    named the number."""

    count: int = DEFAULT_INSIGHTS
    asked: bool = False


def open_question(text: str) -> OpenQuestion | None:
    """The answer shape of ``text`` when it is an open question, else None (see the module
    docstring)."""
    if not text or not _OPEN.search(text) or _LIST.search(text):
        return None
    found = _COUNT.search(text)
    if found is None:
        return OpenQuestion()
    raw = found.group(1).lower()
    n = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
    return OpenQuestion(count=max(1, min(MAX_INSIGHTS, n)), asked=True)
