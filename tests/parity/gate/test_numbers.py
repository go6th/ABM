"""Number consistency between prose and the computed set."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from parity.gate.numbers import (
    check_text,
    computed_numbers,
    computed_percentages,
    mentions,
    run_numbers,
)
from parity.gate.report import FAIL, PASS
from tests.parity.gate.conftest import OTHER_ENTITY, PREFIX, make_docx


def _values(text: str) -> list[tuple[int, str | None]]:
    return [(m.value, m.exempt) for m in mentions(text)]


def test_exempt_classes() -> None:
    assert _values("Since 1998 and in FY2025, on 19 Oct 2026 and 9 February 2026.") == [
        (1998, "year"),
        (19, "date"),
        (2026, "year"),
        (9, "date"),
        (2026, "year"),
    ]
    assert _values("Over 12 months, three weeks, 48 hours.") == [(12, "duration"), (48, "duration")]
    assert _values("$19 billion, 110 basis points, 4,983 million, EUR 20.") == [
        (110, "money"),
        (4983, "money"),
        (20, "money"),
    ]
    assert _values("Tier 1, Part C, Pass 2, section 5, source 2, Q3 2026.") == [
        (1, "label"),
        (2, "label"),
        (5, "label"),
        (2, "label"),
        (2026, "year"),
    ]
    assert _values("3. Company baseline\n2) second") == [(3, "list_marker"), (2, "list_marker")]
    assert _values("ECC 6.0, S/4HANA, R-557322, 1a2b, 2026-10-19, 5.4 percent, 401(k)") == [
        (401, "money")
    ]


def test_thousands_separators_and_percentages() -> None:
    found = mentions("7,145 unique postings and 56 percent of the base, 90%.")
    assert [(m.value, m.percent) for m in found] == [(7145, False), (56, True), (90, True)]
    assert found[0].nouns == ("postings",)


def test_count_claims_bounds_and_plain_numbers() -> None:
    claim, bound, plain = mentions("553 postings removed; at least 5 postings; 150 integrations")
    assert claim.is_count_claim and claim.nouns == ("postings", "removed")
    assert mentions("We excluded 553 Quill (divested) postings.")[0].nouns == (
        "excluded",
        "postings",
    )
    assert bound.bound and not bound.is_count_claim
    assert not plain.nouns and not plain.is_count_claim


def test_computed_set_holds_the_counts_and_their_sums(bundle: dict[str, Any]) -> None:
    known = computed_numbers(bundle)
    assert 569 in known and 100 in known
    assert 669 in known  # all entities together
    assert 60 in known  # the ranking base
    assert 7 in known  # live postings captured (meta)
    assert 553 not in known
    assert {15, 5} <= computed_percentages(bundle)
    assert round(40 / 60 * 100) in computed_percentages(bundle)


def test_the_reference_inconsistency_is_caught(bundle: dict[str, Any]) -> None:
    known = computed_numbers(bundle)
    pct = computed_percentages(bundle)
    good = check_text(f"We excluded 569 {OTHER_ENTITY} postings by wording.", "p", known, pct)
    assert good == []
    bad = check_text(f"We excluded 553 {OTHER_ENTITY} postings by wording.", "p", known, pct)
    assert [f.severity for f in bad] == ["fail"]
    assert bad[0].message.startswith("553 near excluded, postings is not a computed value")
    assert "nearest computed 569" in bad[0].message


def test_plain_and_bounded_numbers_only_warn(bundle: dict[str, Any]) -> None:
    known = computed_numbers(bundle)
    pct = computed_percentages(bundle)
    out = check_text(
        "a 200,000 user platform; more than 150 integrations; 37 percent.", "p", known, pct
    )
    assert [f.severity for f in out] == ["warn", "warn", "warn"]
    assert out[2].message.startswith("37 percent is not a computed value")


def test_run_over_prose_and_rendered_files(bundle: dict[str, Any], tmp_path: Path) -> None:
    summary = make_docx(
        tmp_path / f"{PREFIX}_Account_Summary.docx", ["We removed 569 postings; 4 of 5 people."]
    )
    evidence = make_docx(
        tmp_path / f"{PREFIX}_Sources_and_Evidence.docx", ["Quote: 777 postings in the source."]
    )
    assert run_numbers([summary, evidence], bundle).status == PASS
    bundle["prose"]["narrative"]["wrong"] = ["We excluded 553 postings by wording."]
    result = run_numbers([summary, evidence], bundle)
    assert result.status == FAIL
    assert [f.file for f in result.failures] == ["bundle prose"]
    bad = make_docx(tmp_path / f"{PREFIX}_Lane_Highlights.docx", ["553 postings were removed."])
    assert [f.file for f in run_numbers([summary, bad], bundle).failures] == [
        f"bundle prose, {bad.name}"
    ]
