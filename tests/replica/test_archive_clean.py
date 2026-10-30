"""The cleaning, classification and fact extraction of an archived page (``archive_clean``,
``archive_facts``, ``archive_records.web_record``, ``archive_reconcile``). SYNTHETIC pages only:
a made-up company, made-up people, reserved example hosts."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from account_intelligence import archive_records as AR
from account_intelligence.archive_clean import check_clean, clean_page
from account_intelligence.archive_facts import Account, classify, find_dates, person_name
from account_intelligence.archive_reconcile import reconcile, record_id

T = datetime(2026, 10, 28, 7, 57, tzinfo=UTC)
TITLE = "Acme Corporation (ACME) Appoints Jane Roe as Chief Information and Digital Officer"
ACME = Account("Acme Corporation", "acme.example")
ARTICLE = (
    "Acme Corporation (NYSE: ACME) announced that Jane Roe will join as Executive Vice President "
    "and Chief Information and Digital Officer, effective Sept. 30, 2026. She succeeds Dana R. "
    'Smith, who will retire at the end of 2026. "Jane brings deep experience leading digital '
    'programs at scale," said Mark Lee, Chief Executive Officer of Acme. The company reported '
    "revenue of $2.1 billion for the third quarter."
)
# a table the page flattened into one line (the newlines became spaces)
FLAT = (
    "|# |Detail |AI Analyst View | | --- | --- | --- | "
    "|1 |Jane Roe named CIO/CDO |Signals a push on AI execution. | "
    "|2 |Effective Sept. 30, 2026 |Near-term timing. | "
    "|3 |Succeeds D. R. Smith |Orderly transition. | "
    "This is an early move in digital enablement, and investors need evidence of savings."
)
PAGE = f"""[](https://www.agg.example/)
Products
Chart
Screener
English
[Sign in](https://agg.example/signin)
Search
# {TITLE}
{TITLE}
Agg Example News · Sept. 21, 2026
{ARTICLE}
**Material Details**
{FLAT}
{ARTICLE}
AI-generated summary; may contain inaccuracies. Based on content distributed by PR Newswire.
Related news
Something else entirely about another company
Look First
Made by humans
SOC 2 compliant GDPR
Terms of Use
"""


def test_the_page_chrome_the_repeat_and_the_footer_are_gone() -> None:
    cleaned = clean_page(PAGE, title=TITLE, host="agg.example")
    assert cleaned.body.startswith("Acme Corporation (NYSE: ACME) announced")
    for chrome in (
        "Products",
        "Screener",
        "English",
        "Sign in",
        "Look First",
        "Made by humans",
        "SOC 2",
        "Something else",
    ):
        assert chrome not in cleaned.body
    assert cleaned.body.count("Jane Roe will join") == 1  # the article was on the page twice
    assert cleaned.duplicates_removed == 1 and cleaned.boilerplate_removed >= 8
    assert cleaned.byline == "Agg Example News · Sept. 21, 2026"
    assert cleaned.disclaimer.startswith("AI-generated summary")
    assert cleaned.language == "en"
    assert check_clean(cleaned.body, cleaned.tables) == []


def test_a_flattened_table_is_rebuilt_with_its_title_and_the_prose_after_it_survives() -> None:
    cleaned = clean_page(PAGE, title=TITLE, host="agg.example")
    (table,) = cleaned.tables
    assert table["title"] == "Material Details"
    assert table["columns"] == ["#", "Detail", "AI Analyst View"]
    assert [r[1] for r in table["rows"]] == [
        "Jane Roe named CIO/CDO",
        "Effective Sept. 30, 2026",
        "Succeeds D. R. Smith",
    ]
    assert all(len(r) == 3 for r in table["rows"]) and cleaned.tables_repaired == 1
    assert "early move in digital enablement" in cleaned.body
    assert "|" not in cleaned.body and "Material Details" not in cleaned.body


def test_a_multi_line_pipe_table_and_markdown_escapes_are_read() -> None:
    page = (
        "Intro paragraph with \\#hashtag and **bold** words, long enough to count as text here.\n\n"
        "**Terms**\n| Item | Value |\n| --- | --- |\n| a | 1 |\n| b | 2 |\n\n"
        "After the table the text goes on for a while."
    )
    cleaned = clean_page(page, title="")
    assert cleaned.tables == [
        {"title": "Terms", "columns": ["Item", "Value"], "rows": [["a", "1"], ["b", "2"]]}
    ]
    assert "#hashtag" in cleaned.body and "**" not in cleaned.body and "\\#" not in cleaned.body
    assert "After the table" in cleaned.body


def test_commentary_under_an_analyst_heading_moves_out_of_the_body() -> None:
    view = "This is an early move and investors should wait for evidence of savings first."
    page = (
        "Acme Corporation announced a new plan for its customers across the region this autumn.\n\n"
        f"## AI Analyst View\n\n{view}\n"
    )
    cleaned = clean_page(page, title="")
    assert cleaned.analysis == [
        {
            "heading": "AI Analyst View",
            "text": view,
        }
    ]
    assert "early move" not in cleaned.body


def test_an_ai_summary_is_never_confirmed_and_names_its_original() -> None:
    cls = classify(
        "https://www.agg.example/news/1", kind="page", title=TITLE, raw=PAGE, account=ACME
    )
    assert (cls.source_type, cls.is_ai_generated, cls.is_primary, cls.fact_grade) == (
        "AI-generated summary",
        True,
        False,
        "Lead",
    )
    assert cls.derived_from == {
        "name": "Acme Corporation press release via PR Newswire",
        "url": "Not found",
    }
    raw = PAGE + "\nSource: https://www.prnewswire.com/news-releases/acme-names-jane-roe-301.html"
    found = classify(
        "https://www.agg.example/news/1", kind="page", title=TITLE, raw=raw, account=ACME
    )
    assert found.derived_from and found.derived_from["url"].endswith("acme-names-jane-roe-301.html")


def test_the_companys_own_release_is_primary_and_confirmed() -> None:
    cls = classify(
        "https://www.acme.example/newsroom/jane-roe",
        kind="page",
        title=TITLE,
        raw=ARTICLE,
        account=ACME,
    )
    assert (cls.source_type, cls.is_primary, cls.fact_grade, cls.derived_from) == (
        "Company press release",
        True,
        "Confirmed",
        None,
    )
    wire = classify(
        "https://www.prnewswire.com/news-releases/x.html",
        kind="page",
        title=TITLE,
        raw=f"NEW YORK, Sept. 21, 2026 /PRNewswire/ -- {ARTICLE}",
        account=ACME,
    )
    assert (wire.source_type, wire.is_primary) == ("Company press release", True)
    other = classify(
        "https://www.prnewswire.com/news-releases/y.html",
        kind="page",
        title="Other Co names a CFO",
        raw="Other Co said so.",
        account=ACME,
    )
    assert (other.is_primary, other.fact_grade) == (False, "Probable")


def test_trade_press_is_probable_and_an_unknown_site_is_a_lead() -> None:
    probable = classify(
        "https://www.fiercehealthcare.com/a", kind="page", title="t", raw="x", account=ACME
    )
    unknown = classify("https://random.example/a", kind="page", title="t", raw="x", account=ACME)
    assert (probable.source_type, probable.fact_grade) == ("Trade press", "Probable")
    assert (unknown.source_type, unknown.fact_grade) == ("Other", "Lead")


def test_dates_names_and_the_end_of_a_year() -> None:
    assert {
        d[0]
        for d in find_dates("effective Sept. 30, 2026; retires at the end of 2026; 5 March 2027")
    } == {"2026-09-30", "2026-12-31", "2027-03-05"}
    assert person_name("Jane Roe") == "Jane Roe" and person_name("Dana R. Smith") == "Dana R. Smith"
    for bad in (
        "Chief Information Officer",
        "Appoints Arthur",
        "The Company",
        "Executive Vice President",
        "Becton",
    ):
        assert person_name(bad) is None


def test_the_record_holds_typed_facts_and_nothing_it_was_not_told() -> None:
    record = AR.web_record(
        "https://www.agg.example/news/1",
        PAGE,
        schema=AR.NEWS2,
        title=TITLE,
        captured_at=T,
        published_date="2026-09-21",
        category="leadership_change",
        account=ACME,
    )
    assert record["schema"] == "news-archive/2" and record["collected_at"] == T.isoformat()
    assert record["published_date"] == "2026-09-21" and record["language"] == "en"
    assert "article_text" not in record and "text" not in record  # the article is stored once
    assert record["quality"]["validation"] == "passed" and record["quality"]["tables_repaired"] == 1
    jane, dana = record["people"]
    assert (jane["name"], jane["change_type"], jane["effective_date"], jane["previous_role"]) == (
        "Jane Roe",
        "Appointed",
        "2026-09-30",
        "Not stated",
    )
    assert (
        jane["new_role"].startswith("Executive Vice President")
        and jane["organization"] == "Acme Corporation"
    )
    assert (dana["name"], dana["change_type"], dana["effective_date"]) == (
        "Dana R. Smith",
        "Retiring",
        "2026-12-31",
    )
    assert [e["event_type"] for e in record["events"]] == ["Appointment", "Departure/Retirement"]
    assert all(e["confidence"] == "Lead" for e in record["events"])
    assert {o["role_in_story"] for o in record["organizations"]} == {
        "Subject",
        "Distributor",
        "Publisher",
    }
    assert record["quotes"] == [
        {
            "speaker": "Mark Lee",
            "quote": "Jane brings deep experience leading digital programs at scale",
        }
    ]
    assert (
        record["figures"][0]["value"] == "2.1 billion"
        and record["figures"][0]["period"] == "third quarter"
    )
    assert (
        "Leadership change" in record["tags"]
        and "Technology, digital and IT leadership" in record["lanes"]
    )
    assert record["content_hash"].startswith("sha256:")
    assert json.loads(AR.snapshot_bytes(record))["title"] == TITLE


def test_the_collection_date_is_not_a_publication_date() -> None:
    record = AR.web_record(
        "https://random.example/a",
        "A short page with a sentence of text in it for the record.",
        schema=AR.PAGE2,
        title="A page",
        captured_at=T,
        published_date="2026-10-28",
    )
    assert record["published_date"] == "Not stated"
    assert (
        AR.web_record("https://random.example/a", "x", schema=AR.PAGE2, title="t", captured_at=T)[
            "published_date"
        ]
        == "Not stated"
    )


def test_contact_details_and_refused_text_do_not_reach_the_record() -> None:
    def guard(text: str) -> tuple[str, bool]:
        return text, "secretword" in text

    page = (
        "Write press@acme.example or call +1 555 123 4567 about the plan, some text to keep.\n\n"
        "The secretword paragraph is long enough to be kept as a paragraph of its own here."
    )
    record = AR.web_record(
        "https://random.example/a", page, schema=AR.PAGE2, title="t", captured_at=T, guard=guard
    )
    assert (
        "press@acme.example" not in record["body_markdown"]
        and "[redacted]" in record["body_markdown"]
    )
    assert "secretword" not in record["body_markdown"] and record["body_markdown"].startswith(
        "Write"
    )
    assert record["withheld"] == ["body_markdown", "summary"]  # the lead is cut from the same text


def test_the_index_fields_fit_beside_the_row_and_name_the_columns() -> None:
    record = AR.web_record(
        "https://www.agg.example/news/1",
        PAGE,
        schema=AR.NEWS2,
        title=TITLE,
        captured_at=T,
        published_date="2026-09-21",
        account=ACME,
    )
    meta = AR.index_meta(record)
    assert meta and meta["source_type"] == "AI-generated summary" and meta["fact_grade"] == "Lead"
    assert (
        meta["people"] == "Jane Roe; Dana R. Smith"
        and meta["event_types"] == "Appointment; Departure/Retirement"
    )
    assert len(json.dumps(meta, ensure_ascii=False).encode()) <= AR.META_BUDGET
    wide = dict(
        record, publisher="é" * 500, people=[{"name": "ü" * 300}] * 5, lanes=["ñ" * 300] * 5
    )
    assert len(json.dumps(AR.index_meta(wide), ensure_ascii=False).encode()) <= AR.META_BUDGET


def _rec(url: str, page: str, title: str, published: str | None = None) -> dict:
    return AR.web_record(
        url,
        page,
        schema=AR.NEWS2,
        title=title,
        captured_at=T,
        published_date=published,
        account=ACME,
    )


def test_records_that_report_one_event_are_linked_to_the_primary_source() -> None:
    summary = _rec("https://www.agg.example/news/1", PAGE, TITLE, "2026-09-21")
    release = _rec("https://www.acme.example/newsroom/jane-roe", ARTICLE, TITLE, "2026-09-21")
    unrelated = _rec(
        "https://random.example/x",
        "Acme opened a new office in a city this year, which is all the page says here.",
        "Office",
        "2026-09-22",
    )
    assert release["is_primary"] and not summary["is_primary"]
    changed = reconcile("acme.example", {r["url"]: r for r in (summary, release, unrelated)})
    assert set(changed) == {summary["url"]}
    linked = changed[summary["url"]]
    master = record_id("acme.example", release)
    assert master.startswith("arc_acme.example_") and master.endswith(release["content_hash"][7:19])
    assert (linked["duplicate_of"] or master in linked["corroborates"]) and linked["derived_from"][
        "url"
    ] == release["url"]
    assert summary["derived_from"]["url"] == "Not found"  # the input was not changed


def test_two_dates_more_than_three_days_apart_are_two_events() -> None:
    a = _rec("https://www.agg.example/news/1", PAGE, TITLE, "2026-09-21")
    later = PAGE.replace("Sept. 30, 2026", "Oct. 20, 2026").replace("end of 2026", "end of 2027")
    b = _rec("https://www.other.example/news/2", later, TITLE, "2026-09-21")
    assert reconcile("acme.example", {a["url"]: a, b["url"]: b}) == {}
