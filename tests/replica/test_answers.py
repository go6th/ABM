"""Workspace answers for Ask (``replica.answers``, ``replica.model_answer``): composed only from
stored rows, every statement cited, citations numbered by first use, the offer shown only when
the workspace is stale or could not answer, and a model reply used only when its citations are
real. Pure tests: the workspace is built in memory."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from account_intelligence.replica import answers as A
from account_intelligence.replica.model_answer import context_block, parse

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=UTC)


def acct(
    name: str, domain: str, lanes: list[str], cadence: str = "weekly", days: int = 2, runs: int = 2
) -> A.Acct:
    return A.Acct(
        id=uuid4(),
        domain=domain,
        name=name,
        lanes=lanes,
        cadence=cadence,
        last_refresh=NOW - timedelta(days=days),
        refreshes=runs,
    )


def row(
    kind: str,
    a: A.Acct,
    text: str,
    days: int,
    grade: str | None = None,
    lane: str | None = None,
    attr: str = "",
    title: str | None = None,
) -> A.Row:
    return A.Row(
        key=f"{kind}:{uuid4()}",
        kind=kind,
        account=a,
        text=text,
        title=title or f"{a.name} source",
        meta=f"{text} · x",
        when=NOW - timedelta(days=days),
        url=f"https://{a.domain}/x",
        grade=grade,
        lane=lane,
        extra={"attr": attr, "lanes": lane or "", "key_finding": False},
    )


def workspace() -> tuple[A.Workspace, dict[str, A.Acct]]:
    hm = acct("Highmark", "highmark.example", ["Salesforce", "Data and AI"])
    cb = acct("Cambia Health", "cambiahealth.example", ["Salesforce"], cadence="2w", days=4)
    cg = acct("Cigna", "cigna.example", ["ServiceNow"], days=6, runs=1)
    facts = [
        row(
            "fact",
            hm,
            "New technology leader named in the data organization",
            6,
            "confirmed",
            "data_and_ai",
            "leadership.technology_leader",
            "Leadership announcement",
        ),
        row(
            "fact",
            hm,
            "Two open Salesforce build roles point to a customer data program",
            3,
            "probable",
            "salesforce",
            "hiring.salesforce_build_roles",
        ),
        row(
            "fact",
            cg,
            "[Name], VP IT Service Management, is closest to ServiceNow",
            8,
            "probable",
            "servicenow",
            "people.servicenow_owner",
            "Cigna leadership page",
        ),
        row(
            "fact",
            cg,
            "[Name], Director of Enterprise Platforms, may own ServiceNow",
            16,
            "hypothesis",
            "servicenow",
            "people.enterprise_platforms",
        ),
    ]
    jobs = [
        row("job", hm, "Salesforce Architect", 3, lane="Salesforce"),
        row("job", hm, "Integration Architect", 5, lane="Salesforce"),
        row("job", cb, "Salesforce Developer", 11, lane="Salesforce"),
        row("job", cg, "SAP Finance Analyst", 9, lane="SAP"),
    ]
    signals = [row("signal", hm, "Salesforce Architect role posted", 3, "probable")]
    ws = A.Workspace(
        accounts=[cb, cg, hm],
        facts=facts,
        jobs=jobs,
        people=[],
        signals=signals,
        tech=[],
        mail=[],
        questions={},
        mail_connected=False,
    )
    return ws, {"hm": hm, "cb": cb, "cg": cg}


def text_of(answer: dict) -> str:
    return A.plain_text(answer["paragraphs"])


def test_hiring_answer_cites_each_posting_and_names_accounts():
    ws, _ = workspace()
    out = A.compose_local("Which of my accounts posted Salesforce build roles this month?", ws, NOW)
    body = text_of(out)
    assert body.startswith("Two of your accounts posted Salesforce build roles in the last 30 days")
    assert "Highmark" in body and "Cambia Health" in body and "Cigna" not in body.split("\n\n")[0]
    assert [s["n"] for s in out["sources"]] == list(range(1, len(out["sources"]) + 1))
    assert {"bold"} <= {p["t"] for p in out["paragraphs"][0]}
    assert out["used"] == {"workspace": "used", "web": "not_used", "mail_drive": "not_connected"}


def test_citations_are_numbered_by_first_use():
    ws, _ = workspace()
    out = A.compose_local("Which of my accounts posted Salesforce build roles this month?", ws, NOW)
    cited = [int(p["v"]) for para in out["paragraphs"] for p in para if p["t"] == "cite"]
    seen: list[int] = []
    for n in cited:
        if n not in seen:
            seen.append(n)
    assert seen == list(range(1, len(seen) + 1))


def test_people_answer_says_not_confirmed_and_keeps_grades():
    ws, _ = workspace()
    out = A.compose_local("Who owns ServiceNow at Cigna?", ws, NOW)
    body = text_of(out)
    assert "ServiceNow ownership at Cigna is not confirmed." in body
    assert "(Probable)" in body and "(Hypothesis)" in body
    assert out["account_domain"] == "cigna.example"


def test_offer_shows_when_the_newest_cited_row_is_older_than_the_cadence():
    ws, _ = workspace()
    out = A.compose_local("Who owns ServiceNow at Cigna?", ws, NOW)
    assert out["offer"] == {
        "account_domain": "cigna.example",
        "account_name": "Cigna",
        "refreshed_days_ago": 6,
    }
    fresh = A.compose_local(
        "Which of my accounts posted Salesforce build roles this month?", ws, NOW
    )
    assert fresh["offer"] is None


def test_no_answer_offers_the_web_only_on_confirmation_and_cites_nothing():
    ws, _ = workspace()
    out = A.compose_local("What is their pricing model?", ws, NOW)
    assert out["answered"] is False and out["sources"] == []
    assert out["offer"] is not None
    assert out["used"]["workspace"] == "not_used" and out["used"]["web"] == "not_used"


def test_scope_limits_the_answer_to_one_account():
    ws, a = workspace()
    out = A.compose_local("Which roles were posted this month?", ws, NOW, scope=a["cb"])
    assert {s["account_domain"] for s in out["sources"]} == {"cambiahealth.example"}


def test_followups_never_repeat_the_question_and_are_two():
    ws, _ = workspace()
    q = "Who owns ServiceNow at Cigna?"
    out = A.compose_local(q, ws, NOW)
    assert len(out["followups"]) == 2 and q not in out["followups"]


def test_suggestions_come_from_the_workspace_rows():
    ws, _ = workspace()
    got = A.suggestions(ws, NOW)
    assert "Which of my accounts posted Salesforce build roles this month?" in got
    assert "Who owns ServiceNow at Cigna?" in got
    assert len(got) <= 5


def test_question_categories_are_counted_by_kind_only():
    assert A.classify("Who owns ServiceNow at Cigna?") == "people"
    assert A.classify("Which accounts hired in data and AI?") == "hiring"
    assert A.classify("First call questions for Highmark") == "first_call"
    assert A.classify("Tell me a joke") == "other"


def test_model_reply_is_used_only_with_real_citations():
    ws, _ = workspace()
    block, ids = context_block(ws.facts[:2])
    assert "[R1]" in block and "[R2]" in block
    good = parse(
        "**Highmark** named a new technology leader [R1]. Roles suggest a program [R2].", ids
    )
    assert good is not None and len(good.rows) == 2
    assert parse("**Highmark** named a leader [R9].", ids) is None
    assert parse("No citation at all.", ids) is None
    # headings, lists and tables are kept line for line (owner decision 2026-10-17)
    shaped = parse("# Heading\n\n- bullet [R1]\n- second [R2]", ids)
    assert shaped is not None and shaped.paragraphs[0] == "# Heading"
    assert (
        shaped.paragraphs[1].startswith("- bullet [[") and "\n- second [[" in shaped.paragraphs[1]
    )
    table = parse("| Role | Grade |\n|---|---|\n| Leader | Confirmed [R1] |   ", ids)
    assert table is not None
    key = next(iter(table.rows))
    assert table.paragraphs[0].split("\n") == [
        "| Role | Grade |",
        "|---|---|",
        f"| Leader | Confirmed [[{key}]] |",
    ]


def test_closed_postings_join_only_when_the_question_asks_about_them():
    assert A.wants_closed("Which Salesforce roles were open in the past and are now closed?")
    assert A.wants_closed("Show all the postings since 2025")
    assert not A.wants_closed("What is Highmark hiring for?")
    assert not A.wants_closed("Who owns ServiceNow at Cigna?")


def test_a_whole_file_is_announced_as_a_row_and_given_in_its_own_block():
    from account_intelligence.replica.model_answer import file_blocks

    ws, _ = workspace()
    acct = ws.accounts[0]
    sheet = A.Row(
        key="whole:doc-1",
        kind="file",
        account=acct,
        text="Req | Title | Status\n1 | Architect | Closed\n2 | Lead | Open",
        title="From your files: GSK_Job_Postings.xlsx (the whole file)",
        meta="Internal · 2 passages, read whole",
        when=None,
        url=None,
        grade="internal",
        extra={"whole_file": True, "document": 'GSK <b>"x"</b>.xlsx', "lines": 3},
    )
    block, ids = context_block([ws.facts[0], sheet])
    assert "[R2] account:" in block and "given whole below (3 lines)" in block
    assert "Architect" not in block  # the content is not inlined in the row list
    files = file_blocks(ids)
    assert files.startswith('<file id="R2" name="GSK _b__x___b_.xlsx">')
    assert "1 | Architect | Closed" in files and files.rstrip().endswith("</file>")
    assert file_blocks({"R1": ws.facts[0]}) == ""


def test_model_reply_items_are_read_after_the_marker():
    ws, _ = workspace()
    _, ids = context_block(ws.facts[:1])
    d = parse(
        "Ask about the new leader [R1].\n\nITEMS: FIRST CALL QUESTIONS\n1. What changed?\n2. Who"
        " decides?",
        ids,
    )
    assert d is not None and d.items == (
        "FIRST CALL QUESTIONS",
        ["1. What changed?", "2. Who decides?"],
    )


def test_workspace_answer_makes_no_paid_call():
    """The answer modules import nothing that can reach the web, a data tool or the ledger."""
    root = Path(__file__).resolve().parents[2] / "src" / "account_intelligence" / "replica"
    banned = (
        "account_intelligence.connectors",
        "connectors_client",
        "account_intelligence.agents",
        "account_intelligence.workflow",
        "httpx",
        "requests",
        "parallel",
    )
    for name in ("answers.py", "model_answer.py"):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                mods = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [n.name for n in node.names]
                )
                for mod in mods:
                    assert not mod.startswith(banned), f"{name} imports {mod}"


def test_hiring_question_that_asks_who_to_call_gets_first_call_questions():
    ws, _ = workspace()
    out = A.compose_local(
        "Which of my accounts posted Salesforce build roles this month, and who should I call"
        " first?",
        ws,
        NOW,
    )
    assert text_of(out).startswith("Two of your accounts posted Salesforce build roles")
    assert out["items"] is not None and out["items"]["title"] == "FIRST CALL QUESTIONS"
    assert A.classify("Who is hiring Salesforce developers?") == "hiring"


def test_retrieved_document_passages_get_places_in_the_model_rows():
    """Passages retrieved for the question come right after what the local answer cited, even
    when they are graded internal and older than every research fact (audit 2026-10-15)."""
    from account_intelligence.replica.model_answer import MAX_PASSAGES, pick_rows

    ws, by = workspace()
    first = next(iter(by.values()))
    ws.passages = [
        A.Row(
            key=f"passage:doc:{i}",
            kind="file",
            account=first,
            text=f"Passage {i} about the renewal budget.",
            title="From your files: plan.docx",
            meta="Internal · page 1",
            when=None,
            url=None,
            grade="internal",
            extra={"passage": True},
        )
        for i in range(MAX_PASSAGES + 3)
    ]
    q = A.understand("What does the plan say about the renewal budget?", ws, None)
    picked = pick_rows(q, ws, A.Draft())
    keys = [r.key for r in picked]
    assert keys[:MAX_PASSAGES] == [f"passage:doc:{i}" for i in range(MAX_PASSAGES)]
    assert f"passage:doc:{MAX_PASSAGES}" not in keys  # a bounded number of places
