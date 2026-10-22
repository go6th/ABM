"""``documents.gate.withheld_files``: which client files a verdict's defects hold back."""

from __future__ import annotations

from documents.gate import FILE_CHECKS, withheld_files


def test_file_level_defects_name_their_file_and_run_level_ones_name_none() -> None:
    defects = [
        {"check": "leak_check", "item": "people:ngram=2,embedding=0,documents=1"},
        {"check": "rows_dropped", "item": "people:1"},
        {"check": "leak_check", "item": "people:ngram=3"},  # the same check twice: once
        {"check": "client_terms", "item": "jobs:1"},
        {"check": "tracker_rows", "item": "podcasts:unset"},  # run-level
        {"check": "deliverable_set", "item": "tech"},  # run-level
        {"check": "leak_check", "item": "workspace:ngram=1"},  # not a client file kind
        {"check": "leak_check", "item": ""},
        {"item": "summary:1"},
    ]
    assert withheld_files(defects) == {
        "people": ["leak_check", "rows_dropped"],
        "jobs": ["client_terms"],
    }
    assert withheld_files([]) == {}
    assert "tracker_rows" not in FILE_CHECKS and "leak_check" in FILE_CHECKS
