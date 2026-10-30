"""A file carries a visible Partial note when a tracker row of its area is not covered."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from exports.partial import AREA_ROWS, partial_notes


@dataclass(frozen=True)
class Line:
    key: str
    row: str
    status: str
    reason: str | None = None


def test_every_covered_row_gives_no_note() -> None:
    rows = [Line("job_board_by_lane", "Job board search by lane", "Done"),
            Line("job_postings_12_months", "Job postings", "None found")]  # fmt: skip
    assert partial_notes(rows, "jobs") == ()


def test_an_uncovered_row_of_the_area_is_named_with_its_status_and_reason() -> None:
    rows = [
        Line("people_by_platform", "People by platform", "Not done", "needs people data consent"),
        Line("technology_detections", "Technology detections", "Partial", "search not approved"),
    ]
    [note] = partial_notes(rows, "people")
    assert note.startswith("Partial: ")
    assert "People by platform (not done: needs people data consent)" in note
    assert "Technology" not in note  # another file's area
    [tech] = partial_notes(rows, "tech")
    assert "search not approved" in tech


def test_a_long_list_is_cut_and_counted_and_an_unknown_area_is_silent() -> None:
    rows = [Line(k, k, "Blocked") for k in sorted(AREA_ROWS["people"])]
    [note] = partial_notes(rows, "people")
    assert note.count("(blocked)") == 4 and "and 2 more" in note
    assert partial_notes(rows, "nothing") == ()


@pytest.mark.parametrize("status", ["Done", "done", " None found "])
def test_covered_words_are_case_and_space_insensitive(status: str) -> None:
    assert partial_notes([Line("technology_detections", "x", status)], "tech") == ()
