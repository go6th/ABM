"""The workspace zip: allow-listed members, scrubbed copies, RESTORE.md, the same bytes twice."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from typing import Any

from parity.gate.zipper import build_workspace_zip, restore_md
from tests.parity.gate.conftest import (
    CLIENT_INTERNAL,
    OTHER_ENTITY,
    PREFIX,
    make_workspace,
    write_outputs,
)


def test_restore_md_states_the_canonical_queries_with_computed_numbers(
    bundle: dict[str, Any],
) -> None:
    text = restore_md(bundle)
    assert f"# {PREFIX} (Zorvex Holdings) workspace" in text
    assert "Pursuing client: Client." in text and CLIENT_INTERNAL not in text
    assert (
        f'"Postings naming Salesforce": rows in {PREFIX}_Job_Postings.xlsx, sheet Jobs, where'
        f" Platforms named contains Salesforce and Entity starts with {PREFIX}."
        " Answer on 20 October 2026: 3 (2 open)." in text
    )
    assert '"Senior people"' in text and "4 of 5. Priority A senior: 2." in text
    assert "base 60 postings" in text and "A: SAP, Salesforce. B: Workday. C: Qlik." in text
    assert f"- {OTHER_ENTITY}: never counted as {PREFIX} (569 rows). Divested in 2026." in text
    assert f"- {PREFIX}: counted as {PREFIX} (100 rows)." in text


def test_zip_members_and_scrubbed_copies(bundle: dict[str, Any], tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    write_outputs(out, bundle)
    ws = make_workspace(tmp_path / "ws")
    zipped = build_workspace_zip(bundle, ws, out)
    assert zipped.path == out / f"{PREFIX}_workspace.zip"
    assert zipped.members == [
        "RESTORE.md",
        "build/build_people.py",
        "data/blob.bin",
        "data/pull.json",
        f"deliverables/{PREFIX}_Account_Summary.docx",
        f"deliverables/{PREFIX}_Job_Postings.xlsx",
        f"deliverables/{PREFIX}_People_List.xlsx",
        f"deliverables/{PREFIX}_Sources_and_Evidence.docx",
        f"deliverables/{PREFIX}_Tech_Stack_and_Teams.xlsx",
        "notes/pull_log_phase1.md",
    ]
    assert zipped.scrub.replacements >= 4 and zipped.scrub.dropped_lines == 3
    with zipfile.ZipFile(zipped.path) as z:
        log = z.read("notes/pull_log_phase1.md").decode()
        assert "Pursuing client: Client." in log and "@" not in log and "credits" not in log
        assert z.read("data/blob.bin").startswith(b"\x00\x01")
        assert all(i.date_time == (2026, 10, 19, 0, 0, 0) for i in z.infolist())


def test_same_input_same_bytes(bundle: dict[str, Any], tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    write_outputs(out, bundle)
    ws = make_workspace(tmp_path / "ws")
    first = build_workspace_zip(bundle, ws, out, tmp_path / "one.zip").path.read_bytes()
    second = build_workspace_zip(bundle, ws, out, tmp_path / "two.zip").path.read_bytes()
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()
