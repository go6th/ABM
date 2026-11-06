"""Open questions and deeper thinking in Ask (Ask insight-answers plan and Sonnet deep-thinking
plan, 2026-11-03): an open question is detected in code and answered with the asked number of
insights instead of a table, its facts table built by the code and collapsed under "Sources";
the model's Ask setting (lowest effort, tool rounds, thinking display) reaches the provider; the
thinking summary rides the answer, scrubbed; the suggestion chips never offer an account the
workspace holds nothing on.

GUARANTEE(test_an_open_question_reaches_the_model_with_its_rules_and_setting)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.platform.llm import (
    EFFORT_ORDER,
    ask_effort,
    ask_model_settings,
    ask_procedure_models,
    ask_setting,
)
from account_intelligence.replica import answers as A
from account_intelligence.replica import render as RENDER
from account_intelligence.replica.model_answer import (
    OPEN_QUESTION_RULES,
    TOOLS_FIRST_RULES,
    stream_with_model,
)
from account_intelligence.replica.open_question import (
    DEFAULT_INSIGHTS,
    MAX_INSIGHTS,
    OpenQuestion,
    open_question,
)
from account_intelligence.replica.tools import AccessTools

# the owner's own questions from the claude.ai and Ask transcripts of 2026-11-02 and 2026-11-03
OPEN = [
    ("What are the top three budget insights that you can see from the workspace file?", 3, True),
    (
        "What are the top three budget insights that you can see from the workspace file that is"
        " important when it comes to IT spend. Look at Account Summary and then the rest.",
        3,
        True,
    ),
    ("Give me 5 key takeaways for the SAP lane", 5, True),
    ("What should I lead with on the first call?", DEFAULT_INSIGHTS, False),
    ("Summarize the account for a seller", DEFAULT_INSIGHTS, False),
    ("What are the top 9 priorities?", MAX_INSIGHTS, True),
]
LISTS = [
    "how many Job postings and people contacts, have you captured them in the workspace file?",
    "How many Salesforce opportunities are open as of now?",
    "Show me the two Salesforce job listings.",
    "The people who are from the San Francisco Bay area",
    "Show me the LinkedIn URLs for all the people that you've listed here.",
    "List the budget owners at UCB",
]


@pytest.mark.parametrize(("text", "count", "asked"), OPEN)
def test_open_questions_are_detected_with_their_count(text: str, count: int, asked: bool) -> None:
    assert open_question(text) == OpenQuestion(count=count, asked=asked)


@pytest.mark.parametrize("text", LISTS)
def test_list_count_and_lookup_questions_keep_the_table(text: str) -> None:
    assert open_question(text) is None


def _insight(n: int, grade: str = "Probable") -> dict[str, str]:
    return {
        "conclusion": f"Insight {n}: the CRM program is where the money is moving [R1].",
        "grade": grade,
        "action": "Ask Client which waves are funded for 2027.",
    }


def _payload(*items: dict[str, str], stats: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "headline": "Three budget signals [R1].",
        "stats": stats or [],
        "sections": [{"kind": "insights", "insights": list(items)}],
    }


def test_an_open_answer_is_the_asked_number_of_insights_and_needs_no_table() -> None:
    three = OpenQuestion(3, asked=True)
    assert RENDER.check_shape(_payload(_insight(1), _insight(2), _insight(3)), three) is None
    assert RENDER.check_shape(_payload(_insight(1), _insight(2)), three)[0] == "insights_count"  # type: ignore[index]
    # a question that named no number takes 1 to MAX_INSIGHTS
    assert RENDER.check_shape(_payload(_insight(1), _insight(2)), OpenQuestion()) is None
    table_only = {
        "headline": "h",
        "sections": [{"kind": "table", "columns": ["a"], "rows": [["1 [R1]"]]}],
    }
    assert RENDER.check_shape(table_only, three)[0] == "insights_required"  # type: ignore[index]
    long = dict(_insight(1), conclusion="One. Two. Three. Four.")
    assert RENDER.check_shape(_payload(long, _insight(2), _insight(3)), three)[0] == "bad_insights"  # type: ignore[index]
    graded = _insight(1, grade="Certain")
    assert (
        RENDER.check_shape(_payload(graded, _insight(2), _insight(3)), three)[0] == "bad_insights"
    )  # type: ignore[index]


def test_a_list_question_still_needs_its_table() -> None:
    assert RENDER.check_shape(_payload(_insight(1))) == (
        "no_table",
        RENDER.check_shape(_payload(_insight(1)))[1],  # type: ignore[index]
    )


def test_an_open_answer_drops_the_headline_numbers() -> None:
    tools = AccessTools(lambda: None, [], [], shape=OpenQuestion(1, asked=True))  # type: ignore[arg-type]
    out = tools._render(_payload(_insight(1), stats=[{"label": "Revenue", "value": "€7.7B"}]))
    assert out["terminal"] is True and out["answer"]["stats"] == []
    assert tools.rendered is not None and tools.rendered["stats"] == []


def _acct(name: str, domain: str) -> A.Acct:
    return A.Acct(
        id=uuid4(),
        domain=domain,
        name=name,
        lanes=[],
        cadence=None,
        last_refresh=None,
        refreshes=1,
    )


def test_insights_read_as_numbered_points_and_the_facts_table_is_collapsed() -> None:
    acct = _acct("UCB", "ucb.example")
    row = A.Row("fact:1", "fact", acct, "CRM program", "Summary", "UCB", None, None, "confirmed")
    converted = RENDER.rewrite(_payload(_insight(1, "Confirmed")), {"R1": row})
    assert converted is not None
    draft = RENDER.to_draft(*converted)
    assert any(
        p.startswith("1. Insight 1:") and "Grade: Confirmed. Next step: Ask Client" in p
        for p in draft.paragraphs
    )
    assert RENDER.ensure_table(draft, collapsed=True)
    facts = draft.structured["sections"][-1]  # type: ignore[index]
    assert facts["title"] == RENDER.FACTS_TITLE and facts["collapsed"] is True


def test_the_effort_floor_raises_never_lowers() -> None:
    floored = [m for m, s in ask_model_settings().items() if s.min_effort]
    assert floored, "models.yaml names at least one model with a minimum Ask effort"
    model = floored[0]
    floor = ask_setting(model).min_effort
    assert floor is not None
    below = EFFORT_ORDER[: EFFORT_ORDER.index(floor)]
    above = EFFORT_ORDER[EFFORT_ORDER.index(floor) :]
    assert all(ask_effort(model, e) == floor for e in below)
    assert all(ask_effort(model, e) == e for e in above)
    assert ask_effort(model, None) == floor
    assert ask_effort("a-model-with-no-setting", "medium") == "medium"


@pytest.mark.parametrize(
    "body",
    [
        "m: {min_effort: extreme}",
        "m: {tool_rounds: 0}",
        "m: {tool_rounds: true}",
        "m: {thinking_display: loud}",
        "m: {colour: blue}",
    ],
)
def test_a_bad_ask_setting_is_a_configuration_error(tmp_path: Path, body: str) -> None:
    path = tmp_path / "models.yaml"
    path.write_text("ask_model_settings:\n  " + body + "\n")
    with pytest.raises(ValueError, match="ask_model_settings"):
        ask_model_settings.__wrapped__(path)


@dataclass
class _Event:
    kind: str
    text: str = ""
    usage: Any = None


def test_suggestions_never_offer_an_account_with_no_research() -> None:
    own, ucb = _acct("6th", "6th.example"), _acct("UCB Inc", "ucb.example")
    now = datetime.now(UTC)
    facts = [A.Row("fact:1", "fact", ucb, "Veeva CRM", "Summary", "UCB", now, None, "internal")]
    ws = A.Workspace([own, ucb], facts, [], [], [], [], [], {}, False)
    chips = A.suggestions(ws, now)
    assert not any("6th" in c for c in chips)
    assert "First call questions for UCB Inc" in chips
    empty = A.Workspace([own], [], [], [], [], [], [], {}, False)
    assert A.suggestions(empty, now) == []


@pytest.mark.asyncio
async def test_an_open_question_reaches_the_model_with_its_rules_and_setting() -> None:
    """The procedure model gets the tools-first rule and the open-question procedure with the
    asked count; its Ask setting raises the effort, sets the rounds and asks for summaries; the
    summary rides the reply's draft and reaches the answer without citation marks."""
    model_id = next(m for m in sorted(ask_procedure_models()) if ask_setting(m).min_effort)
    setting = ask_setting(model_id)
    seen: dict[str, Any] = {}

    class Capture:
        async def stream(self, *a: Any, **k: Any):  # type: ignore[no-untyped-def]
            seen.update(k)
            yield _Event("thinking", "Start from the Tier 1 table [R1]; then the releases.")
            yield _Event("text", "Insight one [R1].")
            yield _Event("usage")

    acct = _acct("UCB", "ucb.example")
    now = datetime.now(UTC)
    facts = [A.Row("fact:1", "fact", acct, "Veeva CRM", "Summary", "UCB", now, None, "confirmed")]
    ws = A.Workspace([acct], facts, [], [], [], [], [], {}, False)
    question = OPEN[0][0]
    q, local = A.draft_local(question, ws, now)
    shape = open_question(question)
    events = [
        e
        async for e in stream_with_model(
            Capture(),
            type("M", (), {"id": model_id})(),
            q,
            ws,
            local,
            user_id="u",
            request_id="r",
            tools=[{"name": "search_passages"}, {"name": RENDER.NAME}],
            run_tool=lambda *_: None,
            effort=EFFORT_ORDER[0],
            shape=shape,
        )
    ]
    rules = seen["system"].parts[1]
    assert TOOLS_FIRST_RULES in rules
    assert OPEN_QUESTION_RULES.format(count=3, asked=" (the number the question asks for)") in rules
    assert seen["effort"] == setting.min_effort
    assert seen["max_tool_rounds"] == setting.tool_rounds
    assert seen["thinking_display"] == setting.thinking_display
    kind, reply = events[-1]
    assert kind == "done" and reply.draft.thinking.startswith("Start from the Tier 1 table")
    answer = A.finish(reply.draft, q, ws, now, None)
    assert answer["thinking"] == "Start from the Tier 1 table; then the releases."


def test_the_ask_rules_reach_ask_and_never_a_research_prompt() -> None:
    """The Ask-only rules file (config/skill/ask-answers.md) joins every Ask answer; no research
    task's skill context carries it, so research prompts (and the replies cached under them)
    are unchanged by it."""
    from agents.skill_context import SECTIONS, ask_skill_context, skill_context

    text = ask_skill_context()
    assert "<!-- ask-answers.md -->\n## Answering open questions in Ask" in text
    assert "<!-- ask-answers.md -->\n## Standing reading rules" in text
    assert '"Top N" means exactly N.' in text and "conferences, trade shows" in text
    for task in SECTIONS:
        assert "Answering open questions in Ask" not in skill_context(task), task
