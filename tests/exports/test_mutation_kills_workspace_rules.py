"""Kill tests for the second mutation run (2026-10-30) of ``exports.workspace``,
``exports.numbers``, ``exports.change_note`` and ``exports.policy``: the order of the members,
the release files, the explicit name checks that the strict name pattern would otherwise hide,
the entry attributes and the exact error messages. Hermetic."""

from __future__ import annotations

import io
import json
import re
import struct
import zipfile
import zlib
from datetime import date
from uuid import UUID

import pytest

from exports import workspace as W
from exports.change_note import rows_change_note
from exports.config import ContentRules, ExportConfig
from exports.errors import BannedContent, UnsafePackage
from exports.numbers import NumberRegistry, normal_form, numbers_in
from exports.policy import denied_link, restricted_terms, split_for_cell
from exports.workspace import build_workspace, check_zip, safe_name
from tests.exports.corpus import broker
from tests.exports.test_pure_rules import _manifest
from tests.exports.test_workspace import EXTRAS, _inputs


def _build(
    cfg: ExportConfig,
    *,
    rules: ContentRules | None = None,
    extras=None,
    theme=None,
    releases=None,
    manifest=None,
    data=None,
    deliverables=None,
) -> bytes:  # type: ignore[no-untyped-def]
    files, std = _inputs()
    return build_workspace(
        deliverables=deliverables or files,
        data=data or std,
        manifest=manifest if manifest is not None else {"run_id": "r"},
        rules=rules or cfg.rules,
        theme=theme or cfg.theme.zip,
        account="Synthetic",
        as_of=date(2026, 9, 30),
        extras=extras or EXTRAS,
        releases=releases,
    )


def _names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return z.namelist()


def _error(call) -> UnsafePackage:  # type: ignore[no-untyped-def]
    with pytest.raises(UnsafePackage) as caught:
        call()
    return caught.value


# ---- safe_name: each explicit check refuses on its own ----------------------------------------


@pytest.mark.parametrize(
    "name",
    ["", "x" * 9, "/abs/path.md", "a\\b.md", "a/../b.md", "../b.md", "c:x.md", "a/..", "a:b/c.md"],
)
def test_each_explicit_name_check_refuses_without_the_strict_pattern(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(W, "_SAFE", re.compile(r".*", re.S))
    limit = 8 if name == "x" * 9 else 120
    err = _error(lambda: safe_name(name, limit))
    assert err.message == "unsafe member name" and err.items == (name[:limit],)


def test_a_permissive_pattern_still_lets_a_plain_name_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(W, "_SAFE", re.compile(r".*", re.S))
    assert safe_name("a/b.md", 120) == "a/b.md"
    assert safe_name("a..b/c.md", 120) == "a..b/c.md"  # ".." inside a segment is not a traversal
    assert safe_name("a" * 120, 120) == "a" * 120


def test_the_refusal_messages_are_exact() -> None:
    err = _error(lambda: safe_name("../x", 120))
    assert (err.message, err.items) == ("unsafe member name", ("../x",))
    err = _error(lambda: safe_name("Synthetic_Internal_Only.docx", 120))
    assert (err.message, err.items) == (
        "an internal-only member cannot enter the workspace",
        ("Synthetic_Internal_Only.docx",),
    )
    long = "../" + "x" * 200
    assert _error(lambda: safe_name(long, 50)).items == (long[:50],)


# ---- build_workspace --------------------------------------------------------------------------


def test_the_input_shape_errors_are_exact(cfg: ExportConfig) -> None:
    files, data = _inputs()
    short = {k: v for k, v in files.items() if k != "tech"}
    err = _error(lambda: _build(cfg, deliverables=short))
    assert err.message == "the workspace needs exactly the five client files and their data"
    thin = {k: v for k, v in data.items() if k != "tech"}
    assert _error(lambda: _build(cfg, data=thin)).message == err.message
    missing = {k: v for k, v in EXTRAS.items() if k != "notes/pass1.md"}
    err = _error(lambda: _build(cfg, extras=missing))
    assert err.message == "the workspace needs exactly its configured run records"
    extra = {**EXTRAS, "notes/other.md": b"x"}
    assert _error(lambda: _build(cfg, extras=extra)).message == err.message
    no_restore = {k: v for k, v in EXTRAS.items() if k != "RESTORE.md"}
    assert _error(lambda: _build(cfg, extras=no_restore)).message == err.message


def test_a_duplicate_member_name_is_refused_by_that_message(cfg: ExportConfig) -> None:
    files, _ = _inputs()
    files["evidence"] = files["summary"]
    assert _error(lambda: _build(cfg, deliverables=files)).message == "duplicate member names"


def test_release_files_sit_inside_their_folder_and_inside_the_data_folder(
    cfg: ExportConfig,
) -> None:
    folder = cfg.rules.workspace.releases.folder
    releases = {f"{folder}/2026-b.txt": b"bb", f"{folder}/2026-a.txt": b"aa"}
    names = _names(_build(cfg, releases=releases))
    assert f"{folder}/2026-a.txt" in names and f"{folder}/2026-b.txt" in names
    assert names.index(f"{folder}/2026-a.txt") < names.index(f"{folder}/2026-b.txt")
    top = [n.split("/", 1)[0] for n in names[2:]]  # after RESTORE.md and MANIFEST.json
    order = list(cfg.rules.workspace.folders)
    assert top == sorted(top, key=lambda t: order.index(t))  # folders in the configured order
    # the release files stay inside the data folder, after the run's own data files
    data_members = [n for n in names if n.startswith("data/")]
    assert data_members[-2:] == [f"{folder}/2026-a.txt", f"{folder}/2026-b.txt"]


def test_a_release_outside_its_folder_is_refused_and_named(cfg: ExportConfig) -> None:
    folder = cfg.rules.workspace.releases.folder
    outside = "data/other/" + "x" * 100 + ".txt"
    err = _error(lambda: _build(cfg, releases={outside: b"x"}))
    assert err.message == "a release file sits outside its folder"
    assert err.items == (outside[:80],)
    near = folder + "x/file.txt"  # a sibling folder with the same prefix text
    assert _error(lambda: _build(cfg, releases={near: b"x"})).items == (near,)
    ok = f"{folder}/file.txt"
    assert ok in _names(_build(cfg, releases={ok: b"x"}))


def test_an_empty_or_missing_release_mapping_adds_nothing(cfg: ExportConfig) -> None:
    plain = _names(_build(cfg))
    assert _names(_build(cfg, releases={})) == plain
    assert not any(n.startswith(cfg.rules.workspace.releases.folder) for n in plain)


def test_members_follow_the_folder_order_and_unknown_folders_come_last(cfg: ExportConfig) -> None:
    rules = cfg.rules.model_copy(
        update={
            "workspace": cfg.rules.workspace.model_copy(
                update={"folders": ("notes", "deliverables")}
            )
        }
    )
    names = _names(_build(cfg, rules=rules))[2:]
    tops = [n.split("/", 1)[0] for n in names]
    assert tops == sorted(tops, key=lambda t: {"notes": 0, "deliverables": 1}.get(t, 2))
    assert tops[0] == "notes" and tops[-1] in {"data", "build"}
    # the sort is stable: the members of one folder keep the order they were added in
    deliverables = [n for n in names if n.startswith("deliverables/")]
    assert deliverables == [
        f"deliverables/Synthetic_{k}.{'docx' if k in ('summary', 'evidence') else 'xlsx'}"
        for k in ("summary", "evidence", "people", "jobs", "tech")
    ]


def test_the_manifest_stringifies_what_json_cannot_hold(cfg: ExportConfig) -> None:
    run = UUID("00000000-0000-0000-0000-0000000000a1")
    body = _build(cfg, manifest={"run": run, "day": date(2026, 1, 2)})
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        doc = json.loads(z.read("MANIFEST.json"))
    assert doc["build"] == {"run": str(run), "day": "2026-01-02"}


def test_the_cap_errors_are_exact(cfg: ExportConfig) -> None:
    def capped(**kw: int) -> ContentRules:
        ws = cfg.rules.workspace.model_copy(update=kw)
        return cfg.rules.model_copy(update={"workspace": ws})

    assert _error(lambda: _build(cfg, rules=capped(max_members=3))).message == "too many members"
    assert _error(lambda: _build(cfg, rules=capped(max_member_bytes=5))).message == (
        "member over the size cap"
    )
    assert _error(lambda: _build(cfg, rules=capped(max_total_bytes=100))).message == (
        "package over the total size cap"
    )


def test_entries_carry_the_themes_time_and_the_level_six_compression(cfg: ExportConfig) -> None:
    rng_words = ["alpha", "beta", "gamma", "delta", "kappa", "omega", "sigma", "theta"]
    body = (" ".join(rng_words[(i * 7 + i // 3) % 8] + str(i % 97) for i in range(60_000))).encode()
    files, data = _inputs()
    files["summary"] = (files["summary"][0], body)
    theme = cfg.theme.zip.model_copy(update={"entry_time": (2031, 2, 3, 4, 5, 6)})
    out = _build(cfg, theme=theme, deliverables=files)
    with zipfile.ZipFile(io.BytesIO(out)) as z:
        infos = {i.filename: i for i in z.infolist()}
        assert {i.date_time for i in infos.values()} == {(2031, 2, 3, 4, 5, 6)}
        info = infos[f"deliverables/{files['summary'][0]}"]
    name_len, extra_len = struct.unpack(
        "<HH", out[info.header_offset + 26 : info.header_offset + 30]
    )
    start = info.header_offset + 30 + name_len + extra_len
    stored = out[start : start + info.compress_size]
    expected = zlib.compressobj(6, zlib.DEFLATED, -15)
    assert stored == expected.compress(body) + expected.flush()


# ---- check_zip: the problem text names the first eighty characters ----------------------------


def _zip_with(members: dict[str, bytes], *, symlink: str | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, body in members.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o120777 << 16) if name == symlink else (0o600 << 16)
            z.writestr(info, body)
    return out.getvalue()


def _manifest_for(members: dict[str, bytes]) -> bytes:
    import hashlib

    listing = [
        {"name": n, "sha256": hashlib.sha256(b).hexdigest(), "size": len(b)}
        for n, b in members.items()
    ]
    return json.dumps({"members": listing}).encode()


def test_problem_text_cuts_a_long_name_at_eighty_characters(cfg: ExportConfig) -> None:
    long_valid = "d/" + "n" * 90  # a valid name of 92 characters
    long_unsafe = "u" * 85 + " bad"  # a space: not a safe name
    base = {name: b"x" for name in (long_valid, long_unsafe)}
    required = {n: b"x" for n in ("RESTORE.md", *cfg.rules.workspace.required_members)}
    members = {**required, **base}
    data = _zip_with({**members, "MANIFEST.json": _manifest_for(members)}, symlink=long_valid)
    problems = check_zip(data, cfg.rules)
    assert f"unsafe name {long_unsafe[:80]!r}" in problems
    assert f"symlink {long_valid[:80]!r}" in problems
    tiny = cfg.rules.model_copy(
        update={"workspace": cfg.rules.workspace.model_copy(update={"max_member_bytes": 0 + 1000})}
    )
    big = {**members, long_valid: b"z" * 2000}
    data2 = _zip_with({**big, "MANIFEST.json": _manifest_for(big)})
    assert f"member over cap {long_valid[:80]!r}" in check_zip(data2, tiny)
    # a member the manifest does not list, or lists with another hash
    tampered = {**members, "MANIFEST.json": _manifest_for({**members, long_valid: b"other"})}
    assert f"hash mismatch {long_valid[:80]!r}" in check_zip(_zip_with(tampered), cfg.rules)


# ---- numbers ----------------------------------------------------------------------------------


def test_normal_form_strips_only_percent_zeros_and_commas() -> None:
    assert normal_form("40X%") == "40X"
    assert normal_form("X07") == "X07"
    assert normal_form("1.5X0") == "1.5X"
    assert normal_form("1,200") == "1200"
    assert normal_form("007.500") == "7.5"
    assert normal_form("0") == "0" and normal_form("0.0") == "0"
    assert normal_form("40%") == "40"


def test_a_source_code_between_numbers_is_not_a_number() -> None:
    assert numbers_in("12 S-00012 and 3,400 then 7.50%") == ("12", "3400", "7.5")
    assert numbers_in("S-0001") == ()


def test_registry_conflicts_carry_these_messages() -> None:
    registry = NumberRegistry()
    registry.add_sentence("k", "one")
    with pytest.raises(BannedContent) as sentence:
        registry.add_sentence("k", "two")
    assert (sentence.value.message, sentence.value.items) == (
        "a sentence key was registered with two texts",
        ("k",),
    )
    registry.add("n", 1, "base", "s")
    with pytest.raises(BannedContent) as number:
        registry.add("n", 2, "base", "s")
    assert (number.value.message, number.value.items) == (
        "a number key was registered with two values",
        ("n",),
    )
    registry.add("n", 1, "base", "other sentence")  # the same value and base is not a conflict
    registry.add_sentence("k", "one")


# ---- change note ------------------------------------------------------------------------------


def test_a_kind_missing_from_the_new_rows_counts_as_no_rows() -> None:
    old = _manifest({"tech": (("d1", "x"),)})
    note = rows_change_note(old, {})
    assert "Tech Stack and Teams: 0 rows added, 1 removed, 0 changed" in note
    assert "Account Summary: no change" in note


def test_new_rows_of_unequal_length_name_their_kind() -> None:
    old = _manifest({})
    with pytest.raises(ValueError) as caught:
        rows_change_note(old, {"jobs": (("j1", "j2"), ("a",))})
    assert str(caught.value) == "manifest jobs: row ids and digests differ in length"


# ---- policy -----------------------------------------------------------------------------------


def test_split_for_cell_refuses_a_non_positive_limit_with_this_message() -> None:
    for limit in (0, -1):
        with pytest.raises(ValueError) as caught:
            split_for_cell("text", limit)
        assert str(caught.value) == "limit must be positive"
    assert split_for_cell("abcd", 4) == ("abcd",)
    assert split_for_cell("abcde", 4) == ("abcd", "e")


def test_restricted_terms_keep_four_characters_and_drop_three() -> None:
    assert restricted_terms(["abc", "abcd", "abcde", "  abcd  ", ""], "6th agents") == (
        "abcd",
        "abcde",
    )


def test_a_link_naming_a_data_tool_is_denied() -> None:
    tool = broker("p_scrape")
    assert denied_link(f"https://{tool}.example/page") is True
    assert denied_link("https://acme.example/careers") is False
