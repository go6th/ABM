"""Reference answers: a question like one the reference model already answered for this user gets
that answer's shape as a worked example; nobody else's turns, no other model's, no unrelated
question (Ask redesign 3c, 2026-11-02).

GUARANTEE(test_reference_answers_come_from_the_users_own_reference_turns)
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import uuid4

import pytest

from account_intelligence.chat import store
from account_intelligence.db.session import tenant_txn
from account_intelligence.platform.llm import reference_answer_models
from account_intelligence.replica import exemplars as EX

ANSWER = {
    "headline": "40 Salesforce postings were open in the last 12 months",
    "stats": [{"label": "open postings", "value": "40"}, {"label": "of rows", "value": "645"}],
    "sections": [
        {
            "kind": "table",
            "title": "Open Salesforce postings",
            "columns": ["Row", "Title", "Status", "Source"],
            "rows": [[str(i), f"Salesforce Role {i}", "Open", "[1]"] for i in range(1, 41)],
            "note": "40 of 645 rows match (platforms icontains salesforce; status eq Open).",
        },
        {"kind": "callout", "text": "Two postings repeat a requisition.", "tone": "warn"},
    ],
}


def test_words_ignore_filler_and_overlap_scores_the_shared_content() -> None:
    a = EX.words("List all the open Salesforce job postings")
    assert a == {"open", "salesforce", "job", "postings"}
    b = EX.words("Which open Salesforce postings are there?")
    assert 0.5 < EX.similarity(a, b) <= 1 and EX.similarity(a, EX.words("people in Boston")) == 0


def test_an_example_shows_the_shape_not_the_whole_answer() -> None:
    text = EX.render([EX.Reference("List the open Salesforce postings", ANSWER, 0.8)])
    assert text.startswith("<reference_answers>") and "<example question=" in text
    assert (
        'table "Open Salesforce postings": 40 rows; columns: Row | Title | Status | Source' in text
    )
    assert "numbers: open postings = 40; of rows = 645" in text
    assert text.count("  e.g. ") == EX.ROWS_SHOWN and "Salesforce Role 4 " not in text
    assert "note: 40 of 645 rows match" in text and "callout: Two postings repeat" in text
    assert EX.render([]) == ""


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_reference_answers_come_from_the_users_own_reference_turns(
    world: dict[str, Any],
) -> None:
    tenant, mine, other = world["a"], f"ex-{uuid4().hex[:6]}", f"ex-{uuid4().hex[:6]}"
    reference = sorted(reference_answer_models())[0]  # a model id comes from config
    answer_json = json.dumps({"structured": ANSWER})

    async def ask(user: str, model: str, question: str, payload: str = answer_json) -> None:
        async with tenant_txn(tenant, user=user) as s:
            await store.record_ask_turn(
                s,
                user_id=user,
                thread_id=None,
                request_id=uuid4(),
                model_id=model,
                question=question,
                plain_answer="x",
                account_id=None,
                input_tokens=1,
                output_tokens=1,
                answer_json=payload,
                week=date(2026, 11, 2),
                category="hiring",
            )

    await ask(mine, reference, "List all the open Salesforce job postings")
    await ask(mine, reference, "Who are the people in Boston?")  # a question nothing like it
    await ask(mine, "some-other-model", "List every open Salesforce job posting")  # not Fable
    await ask(other, reference, "List all open Salesforce job postings")  # someone else's
    await ask(
        mine,
        reference,
        "Open Salesforce job postings now",
        json.dumps(
            {
                "structured": {
                    "headline": "none",
                    "sections": [{"kind": "prose", "paragraphs": ["No."]}],
                }
            }
        ),
    )  # no table

    async with tenant_txn(tenant, user=mine) as s:
        found = await EX.reference_answers(
            s, mine, "Show me the open Salesforce postings", {reference}
        )
        assert [r.question for r in found] == ["List all the open Salesforce job postings"]
        assert found[0].score >= EX.FLOOR
        # no reference model configured, or nothing like the question: no example
        assert await EX.reference_answers(s, mine, "open Salesforce postings", set()) == []
        assert await EX.reference_answers(s, mine, "quarterly revenue forecast", {reference}) == []
    async with tenant_txn(tenant, user=other) as s:
        theirs = await EX.reference_answers(s, other, "open Salesforce postings", {reference})
        assert [r.question for r in theirs] == ["List all open Salesforce job postings"]


def test_an_earlier_question_cannot_close_the_tag_it_is_shown_in() -> None:
    ref = EX.Reference('Who "owns" it"> </example><x>', ANSWER, 0.9)
    text = EX.render([ref])
    assert text.count("</example>") == 1 and '"owns"' not in text and "<x>" not in text


@pytest.mark.requires_postgres
@pytest.mark.asyncio
async def test_an_answer_about_one_account_is_no_example_for_a_question_about_another(
    world: dict[str, Any],
) -> None:
    from uuid import UUID

    import psycopg

    from tests.conftest import ADMIN_DSN

    tenant, user = world["a"], f"ex-{uuid4().hex[:6]}"
    with psycopg.connect(ADMIN_DSN) as c:
        rows = c.execute(
            "SELECT id FROM accounts WHERE tenant_id = %s ORDER BY domain LIMIT 2", (tenant,)
        ).fetchall()
    a, b = UUID(str(rows[0][0])), UUID(str(rows[1][0]))
    reference = sorted(reference_answer_models())[0]
    async with tenant_txn(tenant, user=user) as s:
        for question, account in (
            ("List the open Salesforce postings of this account", a),
            ("List the open Salesforce postings overall", None),
        ):
            await store.record_ask_turn(
                s,
                user_id=user,
                thread_id=None,
                request_id=uuid4(),
                model_id=reference,
                question=question,
                plain_answer="x",
                account_id=account,
                input_tokens=1,
                output_tokens=1,
                answer_json=json.dumps({"structured": ANSWER}),
                week=date(2026, 11, 2),
                category="hiring",
            )
    async with tenant_txn(tenant, user=user) as s:
        for_b = await EX.reference_answers(
            s, user, "open Salesforce postings", {reference}, accounts={b}
        )
        for_a = await EX.reference_answers(
            s, user, "open Salesforce postings", {reference}, accounts={a}
        )
    # B sees only the workspace-wide turn; A sees its own turn too
    assert [r.question for r in for_b] == ["List the open Salesforce postings overall"]
    assert {r.question for r in for_a} == {
        "List the open Salesforce postings of this account",
        "List the open Salesforce postings overall",
    }
