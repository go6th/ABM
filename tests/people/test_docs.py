"""The generated people documents are current (spec 13): a config change without regenerating
them fails here."""

from __future__ import annotations

import pytest

from people import docs


def test_people_docs_are_current() -> None:
    assert docs.main(["--check"]) == 0


def test_every_count_has_a_documented_base(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "| `alumni` |" in docs.render_counts()
    monkeypatch.delitem(docs._BASES, "alumni")
    with pytest.raises(ValueError):
        docs.render_counts()


def test_check_reports_a_stale_file(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(docs, "ROOT", tmp_path)
    monkeypatch.setattr(docs, "RUBRIC", tmp_path / "docs/people/rubric.md")
    monkeypatch.setattr(docs, "COUNTS", tmp_path / "docs/people/counts.md")
    assert docs.main(["--check"]) == 1
    assert docs.main([]) == 0 and docs.main(["--check"]) == 0
