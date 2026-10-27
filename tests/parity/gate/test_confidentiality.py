"""The allow-list, refused names, the real client name and the scrub."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

from parity.gate.confidentiality import allowed_member, run_confidentiality
from parity.gate.files import refused_name
from parity.gate.report import FAIL, PASS
from parity.gate.scrub import is_credit_line, residue, scrub_text, scrub_tree
from parity.gate.zipper import build_workspace_zip
from tests.parity.gate.conftest import (
    CLIENT_ALIASES,
    CLIENT_INTERNAL,
    PREFIX,
    make_docx,
    make_workspace,
    write_outputs,
)


def test_refused_names() -> None:
    assert refused_name(f"{PREFIX}_Internal_Only.docx")
    assert refused_name("build/notes_internal_only.md")
    assert not refused_name(f"{PREFIX}_Account_Summary.docx")
    assert not refused_name("notes/pull_log_phase1.md")


def test_allow_list() -> None:
    assert allowed_member("RESTORE.md", PREFIX)
    assert allowed_member(f"deliverables/{PREFIX}_People_List.xlsx", PREFIX)
    assert allowed_member(f"deliverables/{PREFIX}_Lane_Highlights.docx", PREFIX)
    assert allowed_member("data/releases/one.txt", PREFIX)
    assert allowed_member("notes/pass1.md", PREFIX)
    assert not allowed_member(f"deliverables/{PREFIX}_Executive_Brief.pptx", PREFIX)
    assert not allowed_member("deliverables/extra/x.docx", PREFIX)
    assert not allowed_member("bundle.json", PREFIX)
    assert not allowed_member(f"{PREFIX}_Account_Summary.docx", PREFIX)


def test_scrub_replaces_name_aliases_and_emails_and_drops_credit_lines(
    bundle: dict[str, Any],
) -> None:
    text = (
        f"Pursuing client: {CLIENT_INTERNAL}. Alias {CLIENT_ALIASES[0].lower()} and "
        f"{CLIENT_ALIASES[1]}; mail ops@{CLIENT_ALIASES[1]}.\n"
        "| Step | Credits used |\n| 2.1 | 12 credits |\n| 2.2 | 3 calls |\n"
        "Credits remaining: 4,500\n"
    )
    scrubbed, replaced, dropped = scrub_text(text, bundle)
    assert residue(scrubbed, bundle) == []
    assert "Pursuing client: Client. Alias Client and Client; mail Client." in scrubbed
    assert dropped == 3 and replaced == 4
    assert "| 2.2 | 3 calls |" in scrubbed
    assert is_credit_line("| Tool | Credits |") and not is_credit_line("| Tool | Calls |")
    assert not is_credit_line("The credit note was read.")


def test_scrub_tree_copies_binaries_and_scrubs_text(bundle: dict[str, Any], tmp_path: Path) -> None:
    ws = make_workspace(tmp_path / "ws")
    summary = scrub_tree(ws / "data", tmp_path / "out", bundle)
    assert summary.text_files == 1 and summary.binary_copied == ["blob.bin"]
    assert (tmp_path / "out" / "blob.bin").read_bytes().startswith(b"\x00\x01")
    assert residue((tmp_path / "out" / "pull.json").read_text(), bundle) == []


def test_clean_outputs_and_zip_pass(bundle: dict[str, Any], tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    write_outputs(out, bundle)
    ws = make_workspace(tmp_path / "ws")
    zipped = build_workspace_zip(bundle, ws, out)
    result = run_confidentiality(out, bundle, zipped.path)
    assert result.findings == []
    assert result.status == PASS
    assert result.details["internal_files_present"] == []


def test_the_internal_only_file_and_a_planted_client_name_are_refused(
    bundle: dict[str, Any], tmp_path: Path
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    write_outputs(out, bundle)
    internal = make_docx(out / f"{PREFIX}_Internal_Only.docx", [f"For {CLIENT_INTERNAL} only."])
    ws = make_workspace(tmp_path / "ws")
    zipped = build_workspace_zip(bundle, ws, out)
    assert f"deliverables/{internal.name}" not in zipped.members
    assert zipped.refused == [f"build/build_{PREFIX}_Internal_Only.js"]
    result = run_confidentiality(out, bundle, zipped.path)
    assert result.status == PASS  # the internal file may sit in the output folder
    assert result.details["internal_files_present"] == [internal.name]
    # a zip that carries it, and a deliverable that names the client, both fail
    with zipfile.ZipFile(zipped.path, "a") as z:
        z.write(internal, f"deliverables/{internal.name}")
        z.writestr("notes/extra.md", f"Call with {CLIENT_ALIASES[0]} staff, cc a@b.example")
    make_docx(out / f"{PREFIX}_Lane_Highlights.docx", [f"Prepared for {CLIENT_INTERNAL}."])
    result = run_confidentiality(out, bundle, zipped.path)
    assert result.status == FAIL
    messages = sorted(f.message for f in result.findings)
    assert messages == [
        "client_name remains in 2 member(s) of the zip",
        "email remains in 1 member(s) of the zip",
        f"member outside the allow-list: deliverables/{internal.name}",
        "refused member names: 1",
        "the pursuing client's real name appears",
    ]
    assert all(CLIENT_INTERNAL not in m for m in messages)
