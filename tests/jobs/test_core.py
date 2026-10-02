from datetime import date

import pytest

from jobs.classify import classify_rules
from jobs.counts import compute_counts, render_count_sentence
from jobs.dedupe import dedupe
from jobs.match import match_platforms
from jobs.models import ClassificationLabel, Posting, RawPosting
from jobs.normalise import canonical_url, posting_key
from jobs.sentences import extract_platform_sentences
from jobs.window import classify_window, window_start


def raw(key: str, *, description: str = "Salesforce admin.", complete: bool = True) -> RawPosting:
    return RawPosting(
        source_id=key,
        title="Admin",
        description=description,
        url=f"https://example.org/{key}",
        provider_code="p_test",
        raw_ref=f"raw:{key}",
        retrieved_at="2026-01-01T00:00:00Z",
        posted_at="2025-10-01",
        first_seen_at="2025-10-01",
        text_complete=complete,
    )


def post(key: str, **kwargs: object) -> Posting:
    r = raw(key, **kwargs)  # type: ignore[arg-type]
    return Posting.from_raw(r, as_of=date(2026, 10, 1))


def test_calendar_window_clamps_month_end_and_boundaries() -> None:
    assert window_start(date(2024, 2, 29)) == date(2023, 2, 28)
    assert classify_window(date(2025, 10, 1), date(2026, 10, 1)) == "in_window"
    assert classify_window(date(2025, 9, 30), date(2026, 10, 1)) == "out_of_window"
    assert classify_window(None, date(2026, 10, 1)) == "undated"


def test_url_key_and_dedupe_are_stable() -> None:
    assert canonical_url("https://EXAMPLE.org/a/?utm_source=x") == "https://example.org/a"
    a, b = raw("source-a"), raw("source-b")
    assert posting_key(a) != posting_key(b)
    b = b.model_copy(update={"source_id": a.source_id})
    assert len(dedupe([a, b])) == 1
    assert dedupe(dedupe([a, b])) == dedupe([a, b])


def test_description_match_and_verbatim_platform_sentence() -> None:
    text = "Build Salesforce workflows. Work with SAP teams."
    matches = match_platforms("Salesforce jobs", text, {"Salesforce": (), "SAP": (), "Tableau": ()})
    assert matches.description == ("SAP", "Salesforce")
    assert matches.title_only == ()
    spans = extract_platform_sentences(text, matches.description, {"Salesforce": (), "SAP": ()})
    assert [s.text for s in spans] == ["Build Salesforce workflows.", "Work with SAP teams."]
    assert text[spans[0].start : spans[0].end] == spans[0].text


def test_title_only_is_lead_not_classification() -> None:
    matches = match_platforms(
        "Salesforce administrator", "Manage CRM workflows", {"Salesforce": ()}
    )
    assert matches.title_only == ("Salesforce",)
    assert matches.description == ()


def test_conflicting_proposal_cues_are_ambiguous() -> None:
    result = classify_rules(
        "Build and administer the platform",
        [
            {"rule_id": "build", "label": "build", "cues": ["build"]},
            {"rule_id": "admin", "label": "admin_owner", "cues": ["administer"]},
        ],
    )
    assert result.label == "unclear"
    assert result.reason == "ambiguous_rules"


def test_counts_are_row_derived_and_render_base() -> None:
    p = post("one")
    p = p.model_copy(
        update={
            "platforms": ("Salesforce",),
            "sentences": extract_platform_sentences(
                p.description or "", ("Salesforce",), {"Salesforce": ()}
            ),
        }
    )
    counts = compute_counts([p], matched_keys={p.posting_key}, minimum_text_length=1)
    assert counts.read == 1
    assert counts.with_platform_sentence == 1
    assert "1 postings read of 1 matched" in render_count_sentence(counts)


def test_incomplete_text_is_not_read() -> None:
    p = post("excerpt", description="Salesforce excerpt", complete=False)
    counts = compute_counts([p], matched_keys={p.posting_key}, minimum_text_length=1)
    assert counts.read == 0


def test_unknown_text_completeness_and_class_vocab() -> None:
    assert ClassificationLabel("unclear") == ClassificationLabel.UNCLEAR
    with pytest.raises(ValueError):
        ClassificationLabel("manager")
