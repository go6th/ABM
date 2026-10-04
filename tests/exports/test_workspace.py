"""Workspace zip safety and determinism (B8, B10, B13, D-25): 100% coverage target."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import date

import pytest

from exports.config import ContentRules, ExportConfig
from exports.errors import UnsafePackage
from exports.workspace import (
    account_slug,
    build_workspace,
    check_zip,
    neutral_payload_members,
    safe_name,
)

KINDS = ("summary", "evidence", "people", "jobs", "tech")


def _inputs() -> tuple[dict, dict]:  # type: ignore[type-arg]
    files = {
        k: (
            f"Synthetic_{k}.{'docx' if k in ('summary', 'evidence') else 'xlsx'}",
            f"body {k}".encode(),
        )
        for k in KINDS
    }
    data = {k: f'{{"kind": "{k}"}}'.encode() for k in KINDS}
    return files, data


def _zip(cfg: ExportConfig, rules: ContentRules | None = None, **kw: object) -> bytes:
    files, data = _inputs()
    args: dict[str, object] = dict(
        deliverables=files,
        data=data,
        manifest={"run_id": "r"},
        rules=rules or cfg.rules,
        theme=cfg.theme.zip,
        account="Synthetic",
        as_of=date(2026, 9, 30),
    )
    args.update(kw)
    return build_workspace(**args)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "name",
    [
        "",
        "/abs/path",
        "a/../b",
        "..",
        "a\\b",
        "c:/x",
        "-dash-first",
        "a//b",
        "sp ace",
        "x" * 121,
        "deliverables/Synthetic_Internal_Only.docx",
        "Synthetic_Operator_Only.docx",
        "internal-only.md",
        "\u00e9t\u00e9.md",
    ],
)
def test_unsafe_names_are_refused(name: str, cfg: ExportConfig) -> None:
    with pytest.raises(UnsafePackage):
        safe_name(name, cfg.rules.workspace.entry_name_max)


@pytest.mark.parametrize(
    "name",
    [
        "RESTORE.md",
        "deliverables/Synthetic_Internal_Systems.docx",
        "data/raw/ab12.json",
        "Internals.md",
    ],
)
def test_safe_names_pass(name: str, cfg: ExportConfig) -> None:
    assert safe_name(name, cfg.rules.workspace.entry_name_max) == name


def test_account_slug() -> None:
    assert account_slug("Synthetic Alpha & Co., Ltd.") == "Synthetic_Alpha_Co._Ltd"
    assert account_slug("\u0634\u0631\u0643\u0629") == "Account"
    assert len(account_slug("x" * 200)) == 60


def test_neutral_payloads_off_by_default_and_never_org(cfg: ExportConfig) -> None:
    payloads = [("p_search", b'{"a": 1}'), ("p_org", b'{"b": 2}')]
    assert neutral_payload_members(payloads, enabled=False) == []
    on = neutral_payload_members(payloads, enabled=True)
    digest = hashlib.sha256(b'{"a": 1}').hexdigest()
    assert on == [(f"data/raw/{digest}.json", b'{"a": 1}')]
    assert all("p_" not in name for name, _ in on)
    assert cfg.rules.workspace.raw_payloads.neutral_name_copy_enabled is False


def test_contents_order_manifest_and_determinism(cfg: ExportConfig) -> None:
    a = _zip(cfg, raw=[("p_search", b"raw")])
    assert a == _zip(cfg, raw=[("p_search", b"raw")])
    with zipfile.ZipFile(io.BytesIO(a)) as z:
        names = z.namelist()
        assert names[:3] == ["RESTORE.md", "MANIFEST.json", "NOTES.md"]
        assert all(n.startswith(("deliverables/", "data/")) for n in names[3:])
        assert not any(n.startswith("data/raw/") for n in names)  # D-25 default: excluded
        assert {i.date_time for i in z.infolist()} == {tuple(cfg.theme.zip.entry_time)}
        listing = json.loads(z.read("MANIFEST.json"))["members"]
        assert {m["name"] for m in listing} == set(names) - {"RESTORE.md", "MANIFEST.json"}
    assert check_zip(a, cfg.rules) == ()


def test_raw_payloads_when_enabled(cfg: ExportConfig) -> None:
    ws = cfg.rules.workspace.model_copy(
        update={
            "raw_payloads": cfg.rules.workspace.raw_payloads.model_copy(
                update={"neutral_name_copy_enabled": True}
            )
        }
    )
    rules = cfg.rules.model_copy(update={"workspace": ws})
    body = _zip(cfg, rules, raw=[("p_search", b"raw"), ("p_org", b"never")])
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        raw = [n for n in z.namelist() if n.startswith("data/raw/")]
        assert len(raw) == 1 and z.read(raw[0]) == b"raw"


def test_stored_compression(cfg: ExportConfig) -> None:
    theme = cfg.theme.zip.model_copy(update={"compression": "stored"})
    with zipfile.ZipFile(io.BytesIO(_zip(cfg, theme=theme))) as z:
        assert {i.compress_type for i in z.infolist()} == {zipfile.ZIP_STORED}


def test_needs_exactly_the_five_files(cfg: ExportConfig) -> None:
    files, data = _inputs()
    with pytest.raises(UnsafePackage):
        _zip(cfg, deliverables={k: v for k, v in files.items() if k != "tech"})
    with pytest.raises(UnsafePackage):
        _zip(cfg, deliverables={**files, "internal": ("Synthetic_Internal_Only.docx", b"x")})


def test_internal_only_document_cannot_enter(cfg: ExportConfig) -> None:
    files, _ = _inputs()
    files["summary"] = ("Synthetic_Internal_Only.docx", b"x")
    with pytest.raises(UnsafePackage):
        _zip(cfg, deliverables=files)


def test_duplicate_names(cfg: ExportConfig) -> None:
    files, _ = _inputs()
    files["evidence"] = files["summary"]
    with pytest.raises(UnsafePackage):
        _zip(cfg, deliverables=files)


def test_caps(cfg: ExportConfig) -> None:
    ws = cfg.rules.workspace
    for update in ({"max_members": 5}, {"max_member_bytes": 8}, {"max_total_bytes": 50}):
        rules = cfg.rules.model_copy(update={"workspace": ws.model_copy(update=update)})
        with pytest.raises(UnsafePackage):
            _zip(cfg, rules)


def _raw_zip(entries: list[tuple[zipfile.ZipInfo | str, bytes]]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for info, body in entries:
            z.writestr(info, body)
    return out.getvalue()


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_check_zip_finds_every_problem(cfg: ExportConfig) -> None:
    link = zipfile.ZipInfo("data/link")
    link.external_attr = 0o120777 << 16
    manifest = json.dumps({"members": [{"name": "NOTES.md", "sha256": "0" * 64}]}).encode()
    bad = _raw_zip(
        [("../evil", b"x"), (link, b"target"), ("NOTES.md", b"n"), ("MANIFEST.json", manifest)]
    )
    problems = check_zip(bad, cfg.rules)
    assert any(p.startswith("unsafe name") for p in problems)
    assert any(p.startswith("symlink") for p in problems)
    assert any(p.startswith("hash mismatch") for p in problems)
    assert check_zip(_raw_zip([("a.md", b"x")]), cfg.rules) == ("no MANIFEST.json",)
    ws = cfg.rules.workspace.model_copy(
        update={"max_members": 1, "max_member_bytes": 1, "max_total_bytes": 1}
    )
    rules = cfg.rules.model_copy(update={"workspace": ws})
    problems = check_zip(_raw_zip([("a.md", b"xx"), ("a.md", b"yy")]), rules)
    assert {"duplicate names", "too many members", "total over cap"} <= set(problems)
    assert any(p.startswith("member over cap") for p in problems)
