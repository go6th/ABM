"""Confidentiality: what may leave the workspace, and what must never.

* The zip allow-list: ``RESTORE.md``; ``deliverables/`` holding the standing files and the
  Lane Highlights when present; ``data/``, ``build/`` and ``notes/``. Anything else in the zip
  is a finding.
* Refused names: no member of a zip, and no file in a share, may carry ``Internal_Only`` or a
  guarded person's name (``files.refused_name``).
* The pursuing client's real name and its aliases appear in no client file (Word and Excel
  text) and in no text member of the zip; after the scrub no ``@`` email address remains in
  the zip's ``notes/``, ``data/`` and ``build/`` members or in ``RESTORE.md``. An email address
  inside a deliverable (a recruiter's address in posting text) is a warning, not a failure:
  the scrub does not rewrite rendered files. Office members of the zip are read through
  their XML.

Findings carry kinds and counts, never the name.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from parity.gate.files import (
    Bundle,
    client_files,
    docx_text,
    optional_names,
    prefix_of,
    refused_name,
    standing_names,
    text_of,
)
from parity.gate.report import CheckResult, Finding, result
from parity.gate.scrub import client_terms, is_text_file, residue

NAME = "confidentiality"
ALLOWED_ROOTS = ("data", "build", "notes")
_XML_TAG = re.compile(r"<[^>]+>")


def allowed_member(name: str, prefix: str) -> bool:
    """Whether a zip member name is on the allow-list."""
    parts = PurePosixPath(name).parts
    if not parts or name.endswith("/"):
        return True
    if parts == ("RESTORE.md",):
        return True
    if parts[0] == "deliverables":
        return len(parts) == 2 and parts[1] in standing_names(prefix) + optional_names(prefix)
    return parts[0] in ALLOWED_ROOTS and len(parts) > 1


def refused_members(names: Iterable[str]) -> list[str]:
    """The names that may never be in a zip or a share."""
    return [n for n in names if refused_name(n)]


def member_text(name: str, data: bytes) -> str | None:
    """The text of a zip member: Word and Excel through their XML, text files decoded, other
    binaries None."""
    lower = name.lower()
    if lower.endswith((".docx", ".xlsx", ".pptx")):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                parts = []
                for member in sorted(z.namelist()):
                    if member.endswith((".xml", ".rels")):
                        parts.append(_XML_TAG.sub(" ", z.read(member).decode("utf-8", "ignore")))
                return "\n".join(parts)
        except zipfile.BadZipFile:
            return None
    if b"\0" in data[:4096]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def check_zip(zip_path: Path, bundle: Bundle) -> list[Finding]:
    """Allow-list, refused names, and residue of the real name, aliases and emails."""
    findings: list[Finding] = []
    prefix = prefix_of(bundle)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        for name in names:
            if not allowed_member(name, prefix):
                findings.append(
                    Finding(NAME, f"member outside the allow-list: {name}", zip_path.name)
                )
        refused = refused_members(names)
        if refused:
            findings.append(Finding(NAME, f"refused member names: {len(refused)}", zip_path.name))
        kinds: dict[str, int] = {}
        deliverable_emails = 0
        for name in names:
            if name.endswith("/"):
                continue
            text = member_text(name, z.read(name))
            if text is None:
                continue
            scrubbed = PurePosixPath(name).parts[0] in ALLOWED_ROOTS or name == "RESTORE.md"
            for kind in residue(text, bundle):
                if kind == "email" and not scrubbed:
                    deliverable_emails += 1
                else:
                    kinds[kind] = kinds.get(kind, 0) + 1
        for kind, n in sorted(kinds.items()):
            findings.append(
                Finding(NAME, f"{kind} remains in {n} member(s) of the zip", zip_path.name)
            )
        if deliverable_emails:
            findings.append(
                Finding(
                    NAME,
                    f"email addresses inside {deliverable_emails} deliverable(s) (posting text;"
                    " the scrub covers notes, data and build only)",
                    zip_path.name,
                    "warn",
                )
            )
    return findings


def check_client_files(out_dir: Path, bundle: Bundle) -> list[Finding]:
    """The real client name and aliases in no client file."""
    findings: list[Finding] = []
    if not client_terms(bundle):
        return findings
    for path in client_files(out_dir):
        text = text_of(path) if path.suffix.lower() == ".xlsx" else docx_text(path.read_bytes())
        if "client_name" in residue(text, bundle):
            findings.append(Finding(NAME, "the pursuing client's real name appears", path.name))
    return findings


def check_scrubbed_dir(root: Path, bundle: Bundle) -> list[Finding]:
    """Residue in a scrubbed folder's text files (for a share built without a zip)."""
    findings: list[Finding] = []
    kinds: dict[str, int] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if refused_name(path.name):
            findings.append(
                Finding(NAME, f"refused file name under {root.name}", str(path.relative_to(root)))
            )
        if is_text_file(path):
            for kind in residue(path.read_text(encoding="utf-8", errors="replace"), bundle):
                kinds[kind] = kinds.get(kind, 0) + 1
    for kind, n in sorted(kinds.items()):
        findings.append(Finding(NAME, f"{kind} remains in {n} file(s)", root.name))
    return findings


def run_confidentiality(
    out_dir: Path, bundle: Bundle, zip_path: Path | None = None, scrubbed: Iterable[Path] = ()
) -> CheckResult:
    """The check over the client files, the zip when given and any scrubbed folders."""
    findings = check_client_files(out_dir, bundle)
    details: dict[str, Any] = {
        "internal_files_present": sorted(p.name for p in out_dir.iterdir() if refused_name(p.name)),
    }
    if zip_path is not None:
        findings += check_zip(zip_path, bundle)
        details["zip"] = zip_path.name
    for root in scrubbed:
        findings += check_scrubbed_dir(root, bundle)
    return result(NAME, findings, details)
