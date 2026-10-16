"""Numbers (B6), citations (B4, B5) and the change note: pure modules, 100% coverage target."""

from __future__ import annotations

from datetime import date

import pytest

from exports.change_note import change_note
from exports.citations import SourceIndex, orphans, unknown_codes
from exports.content import SourceRow
from exports.errors import BannedContent
from exports.models import BuildManifest
from exports.numbers import NumberRegistry, normal_form, numbers_in, unregistered

H = "0" * 64


class TestNumbers:
    @pytest.mark.parametrize(
        ("token", "normal"),
        [
            ("1,200", "1200"),
            ("07", "7"),
            ("3.50", "3.5"),
            ("40%", "40"),
            ("0", "0"),
            ("0.0", "0"),
            ("12.05", "12.05"),
        ],
    )
    def test_normal_form(self, token: str, normal: str) -> None:
        assert normal_form(token) == normal

    def test_numbers_in_skips_codes_and_glued_tokens(self) -> None:
        text = "In Q3 the 3rd team (S-0042) hired 1,200 people, 40% of 3,000; p95 is 2.5."
        assert numbers_in(text) == ("1200", "40", "3000", "2.5")

    def test_registry_add_items_allowed(self) -> None:
        r = NumberRegistry()
        r.add("b.count", 12, "people listed", "12 people of 30 found")
        r.add("a.count", 3, "rows", "3 rows")
        r.add("b.count", 12, "people listed", "12 people of 30 found")  # same: fine
        assert [i.key for i in r.items()] == ["a.count", "b.count"]
        r.add_sentence("s.1", "7 read of 9 matched")
        r.add_sentence("s.1", "7 read of 9 matched")
        assert r.sentences() == ("7 read of 9 matched",)
        assert r.allowed() == frozenset({"12", "30", "3", "7", "9"})

    def test_registry_conflicts_raise(self) -> None:
        r = NumberRegistry()
        r.add("k", 1, "base", "1 thing")
        with pytest.raises(BannedContent):
            r.add("k", 2, "base", "2 things")
        with pytest.raises(BannedContent):
            r.add("k", 1, "other base", "1 thing")
        r.add_sentence("s", "one")
        with pytest.raises(BannedContent):
            r.add_sentence("s", "two")

    def test_unregistered(self) -> None:
        allowed = frozenset({"12"})
        assert unregistered(
            "12 people and 2025 revenue of 4", allowed, ["In 2025 revenue grew"]
        ) == ("4",)
        assert unregistered("no numbers", allowed, []) == ()


class TestCitations:
    def _index(self) -> SourceIndex:
        return SourceIndex(
            [
                SourceRow(
                    code="S-0001",
                    url="https://a.example/x?utm_source=z",
                    published=None,
                    kind="primary",
                ),
                SourceRow(
                    code="S-0002", url="https://B.example/y/", published=None, kind="secondary"
                ),
            ]
        )

    def test_lookup_by_normalised_url(self) -> None:
        idx = self._index()
        assert idx.code_for("https://a.example/x") == "S-0001"
        assert idx.code_for("https://b.example/y") == "S-0002"
        assert idx.code_for("https://c.example/") is None
        assert idx.code_for(None) is None and idx.code_for("") is None
        assert idx.code_for("not a url") is None
        assert idx.knows("S-0001") and not idx.knows("S-9999")

    def test_unknown_codes(self) -> None:
        v = unknown_codes(["S-0001", "S-9999", "S-9999"], self._index(), "w")
        assert [(x.code, x.item) for x in v] == [("unknown_source_code", "S-9999")]

    def test_orphans(self) -> None:
        v = orphans({"people": ["S-0002", "S-0003"], "jobs": ["S-0001"]}, ["S-0001", "S-0002"])
        assert [(x.where, x.item) for x in v] == [("people", "S-0003")]
        assert orphans({}, []) == ()


def _manifest(rows: dict[str, tuple[tuple[str, str], ...]]) -> BuildManifest:
    kinds = ("summary", "evidence", "people", "jobs", "tech")
    return BuildManifest(
        run_id="r",
        account="Synthetic",
        as_of=date(2026, 9, 30),
        snapshot_hash=H,
        theme_hash=H,
        rules_hash=H,
        rows_per_file={k: len(rows.get(k, ())) for k in kinds},
        snapshot_counts={},
        row_ids={k: tuple(i for i, _ in rows.get(k, ())) for k in kinds},
        row_digests={k: tuple(d for _, d in rows.get(k, ())) for k in kinds},
        refs={k: () for k in kinds},
        source_codes_cited=(),
        numbers=(),
        count_sentences=(),
        tracker_tally=("x",),
        withheld_sentences=0,
        long_text_parts=0,
        formula_like_cells=0,
    )


class TestChangeNote:
    def test_first_delivery(self) -> None:
        assert change_note(None, _manifest({})) == "First delivery for this account."

    def test_counts_per_file(self) -> None:
        old = _manifest({"people": (("p1", "a"), ("p2", "b"), ("p3", "c"))})
        new = _manifest({"people": (("p1", "a"), ("p2", "B"), ("p4", "d"), ("p5", "e"))})
        note = change_note(old, new)
        assert note.startswith("Changes since the previous delivery: Account Summary: no change")
        assert "People List: 2 rows added, 1 removed, 1 changed" in note
        assert note.endswith("Tech Stack and Teams: no change.")

    def test_mismatched_manifest_is_refused(self) -> None:
        broken = _manifest({}).model_copy(update={"row_ids": {"summary": ("x",)}})
        with pytest.raises(ValueError):
            change_note(broken, _manifest({}))


def test_restricted_terms_drop_the_platforms_own_name_and_tiny_terms() -> None:
    from exports.policy import restricted_terms

    terms = ["6th", "6th agents", "plausible", "Workday", "ab", "  ", "mckesson.ca", "Agents"]
    assert restricted_terms(terms, "6th agents") == ("mckesson.ca", "plausible", "Workday")
    assert restricted_terms(["jane roe", "Jane Roe"], "6th agents") == ("Jane Roe", "jane roe")
