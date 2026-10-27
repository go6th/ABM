"""The one cleaner and its workbook form."""

from __future__ import annotations

from parity.text import clean, clean_cell, clean_posting, strip_posting_marks


def test_clean_replaces_dashes_spaced_dash_apostrophe_and_arrow() -> None:
    assert clean("Sales – EMEA—North") == "Sales, EMEA, North"
    assert clean("Sales - EMEA") == "Sales, EMEA"
    assert clean("BD’s team") == "BD's team"
    assert clean("ECC → S/4") == "ECC  to  S/4"
    assert clean(None) == ""


def test_clean_cell_keeps_spaced_dash_apostrophe_and_arrow() -> None:
    assert clean_cell("Sales – EMEA - West’s → East") == "Sales, EMEA - West’s → East"
    assert clean_cell(None) == ""
    assert clean_cell(clean_cell("a — b")) == clean_cell("a — b")


def test_posting_marks_are_stripped_before_the_dashes() -> None:
    assert strip_posting_marks("**Role** ## item # C#") == "Role  item  C"
    assert clean_posting("**SAP** – basis") == "SAP, basis"
