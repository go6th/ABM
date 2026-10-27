"""Tests that kill the mutants that survived the first mutation run (spec 9.11) of policy,
numbers, citations, change_note and workspace: exact violation tuples (the ``where`` and the row
id are what list the failing rows), boundaries, asymmetric change data, error items and the
manifest's exact shape."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date

import pytest

from account_intelligence.grading.config import GradingConfig
from exports.change_note import change_note
from exports.citations import SourceIndex, orphans, unknown_codes
from exports.config import Abbreviation, Abbreviations, ContentRules, ExportConfig, StalePhrases
from exports.content import FactRow
from exports.errors import BannedContent, UnsafePackage
from exports.numbers import NumberRegistry
from exports.policy import (
    Violation,
    authored_violations,
    fact_violations,
    split_for_cell,
    visibility_violations,
)
from exports.workspace import (
    account_slug,
    build_workspace,
    check_zip,
    neutral_payload_members,
    safe_name,
)
from tests.exports.test_pure_rules import _manifest
from tests.exports.test_workspace import EXTRAS, KINDS, _inputs


def _fact(**kw: object) -> FactRow:
    base: dict[str, object] = dict(
        id="f9",
        attribute_key="tech.x",
        value_text="v",
        lane=None,
        claim_kind="sourced",
        product_grade="confirmed",
        engine_grade="A",
        read_in_primary_this_run=True,
        may_state_to_prospect=True,
        confirming_question=None,
    )
    base.update(kw)
    return FactRow.model_validate(base)


def test_split_boundaries() -> None:
    assert split_for_cell("abcde", 5) == ("abcde",)  # exactly at the limit: one part
    assert split_for_cell("abc", 1) == ("a", "b", "c")  # a limit of one is valid
    with pytest.raises(ValueError, match="limit must be positive"):
        split_for_cell("abc", 0)
    with pytest.raises(ValueError, match="limit must be positive"):
        split_for_cell("abc", -1)


def test_authored_violation_tuples(rules: ContentRules) -> None:
    r = rules.model_copy(
        update={
            "stale_phrases": StalePhrases(source="t", phrases=("as of today",)),
            "abbreviations": Abbreviations(
                source="t",
                entries=(Abbreviation(abbr="ERP", expansion="enterprise resource planning"),),
            ),
        }
    )
    text = "Plans — an ERP - as of today, leaving Salesforce."
    assert authored_violations(text, r, "sec.1", is_partner=True) == (
        Violation("banned_dash", "sec.1", "dash character"),
        Violation("banned_dash", "sec.1", "spaced hyphen"),
        Violation("stale_phrase", "sec.1", "as of today"),
        Violation("abbreviation_first_use", "sec.1", "ERP"),
        Violation("partner_framing", "sec.1", "leaving Salesforce"),
    )


@pytest.mark.parametrize(
    ("update", "code"),
    [
        ({"product_grade": "certain"}, "grade_vocabulary"),
        ({"read_in_primary_this_run": False}, "confirmed_without_primary_read"),
        ({"may_state_to_prospect": False}, "statement_flag_disagrees"),
        ({"flags": ("conflicting",)}, "question_missing"),
    ],
)
def test_fact_violation_tuples(
    update: dict[str, object], code: str, grading: GradingConfig
) -> None:
    item = "certain" if code == "grade_vocabulary" else "f9"
    assert fact_violations(_fact(**update), grading) == (Violation(code, "fact f9", item),)


def test_internal_fact_violation_tuple(grading: GradingConfig) -> None:
    internal = _fact(
        product_grade="internal", may_state_to_prospect=False, read_in_primary_this_run=False
    )
    assert Violation("internal_in_public_file", "fact f9", "f9") in fact_violations(
        internal, grading
    )


def test_visibility_and_citation_tuples() -> None:
    assert visibility_violations([("r1", "private")], "people") == (
        Violation("non_public_row", "people", "r1"),
    )
    idx = SourceIndex([])
    assert unknown_codes(["S-0007"], idx, "jobs") == (
        Violation("unknown_source_code", "jobs", "S-0007"),
    )
    assert orphans({"tech": ["S-0002"]}, []) == (Violation("orphan_citation", "tech", "S-0002"),)


def test_registry_conflicts_name_the_key() -> None:
    r = NumberRegistry()
    r.add("people.listed", 3, "base", "3 listed")
    with pytest.raises(BannedContent, match="a number key was registered with two values") as e:
        r.add("people.listed", 4, "base", "4 listed")
    assert e.value.items == ("people.listed",)
    r.add_sentence("jobs.sentence.000", "a")
    with pytest.raises(BannedContent, match="a sentence key was registered with two texts") as e:
        r.add_sentence("jobs.sentence.000", "b")
    assert e.value.items == ("jobs.sentence.000",)


def test_change_note_exact_text_with_asymmetric_rows() -> None:
    old = _manifest(
        {"people": (("p1", "a"), ("p2", "b"), ("p3", "c"), ("p4", "d")), "jobs": (("j1", "x"),)}
    )
    new = _manifest(
        {"people": (("p1", "a"), ("p2", "b"), ("p3", "C"), ("p5", "e")), "jobs": (("j1", "x"),)}
    )
    assert change_note(old, new) == (
        "Changes since the previous delivery: Account Summary: no change; Sources and Evidence:"
        " no change; People List: 1 rows added, 1 removed, 1 changed; Job Postings: no change;"
        " Tech Stack and Teams: no change."
    )


def test_change_note_tolerates_a_kind_missing_from_an_old_manifest() -> None:
    old = _manifest({}).model_copy(update={"row_ids": {}, "row_digests": {}})
    new = _manifest({"tech": (("d1", "x"),)})
    assert "Tech Stack and Teams: 1 rows added, 0 removed, 0 changed" in change_note(old, new)


def test_change_note_refuses_mismatched_lengths_with_the_kind() -> None:
    broken = _manifest({}).model_copy(update={"row_ids": {"people": ("p1",)}})
    with pytest.raises(ValueError, match="manifest people: row ids and digests differ in length"):
        change_note(broken, _manifest({}))


def test_safe_name_length_boundary(cfg: ExportConfig) -> None:
    n = cfg.rules.workspace.entry_name_max
    assert safe_name("a" * n, n) == "a" * n
    with pytest.raises(UnsafePackage):
        safe_name("a" * (n + 1), n)


def test_account_slug_keeps_letters_at_the_ends() -> None:
    assert account_slug("Xenon X") == "Xenon_X"
    assert account_slug("..Synthetic Co.._") == "Synthetic_Co"


def test_never_shipped_payloads_are_skipped_not_a_stop() -> None:
    out = neutral_payload_members([("p_org", b"never"), ("p_search", b"kept")], enabled=True)
    assert [b for _, b in out] == [b"kept"]


def test_manifest_shape_and_error_messages(cfg: ExportConfig) -> None:
    files, data = _inputs()
    body = build_workspace(
        deliverables=files,
        data=data,
        manifest={"run_id": "r"},
        rules=cfg.rules,
        theme=cfg.theme.zip,
        account="Synthetic",
        as_of=date(2026, 9, 30),
        extras=EXTRAS,
    )
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        raw = z.read("MANIFEST.json").decode()
    doc = json.loads(raw)
    assert set(doc) == {"schema_version", "account", "as_of", "members", "build"}
    assert (doc["schema_version"], doc["account"], doc["as_of"], doc["build"]) == (
        1,
        "Synthetic",
        "2026-09-30",
        {"run_id": "r"},
    )
    assert all(set(m) == {"name", "sha256", "size"} for m in doc["members"])
    assert raw.startswith('{\n "account"') and raw.endswith("}\n")  # indented, sorted, newline
    with pytest.raises(UnsafePackage, match="exactly the five client files and their data"):
        build_workspace(
            deliverables={k: v for k, v in files.items() if k != KINDS[0]},
            data=data,
            manifest={},
            rules=cfg.rules,
            theme=cfg.theme.zip,
            account="S",
            as_of=date(2026, 9, 30),
            extras=EXTRAS,
        )
    files["evidence"] = files["summary"]
    with pytest.raises(UnsafePackage, match="duplicate member names"):
        build_workspace(
            deliverables=files,
            data=data,
            manifest={},
            rules=cfg.rules,
            theme=cfg.theme.zip,
            account="S",
            as_of=date(2026, 9, 30),
            extras=EXTRAS,
        )


def _ws(cfg: ExportConfig, **update: int) -> ContentRules:
    return cfg.rules.model_copy(update={"workspace": cfg.rules.workspace.model_copy(update=update)})


def _sizes(cfg: ExportConfig) -> tuple[int, int, int]:
    """(members, largest member, total) of the standard test package."""
    files, data = _inputs()
    body = build_workspace(
        deliverables=files,
        data=data,
        manifest={"run_id": "r"},
        rules=cfg.rules,
        theme=cfg.theme.zip,
        account="Synthetic",
        as_of=date(2026, 9, 30),
        extras=EXTRAS,
    )
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        sizes = [i.file_size for i in z.infolist()]
    return len(sizes), max(sizes), sum(sizes)


def test_caps_are_inclusive_at_the_boundary_and_name_the_offender(cfg: ExportConfig) -> None:
    files, data = _inputs()
    members, largest, total = _sizes(cfg)

    def build(rules: ContentRules) -> bytes:
        return build_workspace(
            deliverables=files,
            data=data,
            manifest={"run_id": "r"},
            rules=rules,
            theme=cfg.theme.zip,
            account="Synthetic",
            as_of=date(2026, 9, 30),
            extras=EXTRAS,
        )

    build(_ws(cfg, max_members=members, max_member_bytes=largest, max_total_bytes=total))
    with pytest.raises(UnsafePackage, match="too many members") as e:
        build(_ws(cfg, max_members=members - 1))
    assert e.value.items == (str(members),)
    with pytest.raises(UnsafePackage, match="member over the size cap") as e:
        build(_ws(cfg, max_member_bytes=largest - 1))
    assert len(e.value.items) == 1 and e.value.items[0]
    with pytest.raises(UnsafePackage, match="package over the total size cap") as e:
        build(_ws(cfg, max_total_bytes=total - 1))
    assert e.value.items == (str(total),)


def test_refused_names_are_listed(cfg: ExportConfig) -> None:
    with pytest.raises(UnsafePackage, match="unsafe member name") as e:
        safe_name("../x", 120)
    assert e.value.items == ("../x",)
    with pytest.raises(UnsafePackage, match="an internal-only member cannot enter") as e:
        safe_name("Synthetic_Internal_Only.docx", 120)
    assert e.value.items == ("Synthetic_Internal_Only.docx",)


def _std(cfg: ExportConfig) -> bytes:
    files, data = _inputs()
    return build_workspace(
        deliverables=files,
        data=data,
        manifest={"run_id": "r"},
        rules=cfg.rules,
        theme=cfg.theme.zip,
        account="Synthetic",
        as_of=date(2026, 9, 30),
        extras=EXTRAS,
    )


def test_produced_entries_have_the_fixed_attributes(cfg: ExportConfig) -> None:
    with zipfile.ZipFile(io.BytesIO(_std(cfg))) as z:
        for info in z.infolist():
            assert info.external_attr == 0o600 << 16 and info.create_system == 0
            assert info.compress_type == zipfile.ZIP_DEFLATED
            assert info.date_time == tuple(cfg.theme.zip.entry_time)


def test_check_zip_verifies_every_member_after_the_restore_note(cfg: ExportConfig) -> None:
    good = _std(cfg)
    assert check_zip(good, cfg.rules) == ()
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(good)) as src, zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            body = src.read(info)
            if info.filename == "build/tech.json":
                body = b'{"tampered": true}'
            dst.writestr(info, body)
    assert check_zip(out.getvalue(), cfg.rules) == ("hash mismatch 'build/tech.json'",)


def test_check_zip_caps_are_inclusive_and_sum_every_member(cfg: ExportConfig) -> None:
    members, largest, total = _sizes(cfg)
    good = _std(cfg)
    exact = _ws(cfg, max_members=members, max_member_bytes=largest, max_total_bytes=total)
    assert check_zip(good, exact) == ()
    assert "total over cap" in check_zip(good, _ws(cfg, max_total_bytes=total - 1))
    assert "too many members" in check_zip(good, _ws(cfg, max_members=members - 1))
    over = check_zip(good, _ws(cfg, max_member_bytes=largest - 1))
    assert len(over) == 1 and over[0].startswith("member over cap")
