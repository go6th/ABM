"""Text checks on the rendered Word files (the port of the skill's ``doc_checks.py``).

The text checked is the PDF rendering's (LibreOffice, for checking only; the PDF is never
delivered), so what the reader sees is what is checked. Without a converter the Word XML text is
checked instead and the result says so.

1. Orphan citations: every cited ID (``S12``, ``J4``, ``U2``, alone or in brackets such as
   ``(S9, J2)``) must be a key of ``sources`` or ``jobs_read``. The bundle has no U map, so a U
   citation is always an orphan.
2. Banned dashes: an em dash, an en dash, or a hyphen between spaces used as punctuation. The
   verbatim quotes of ``prose.evidence.extracts`` are blanked first, as the skill did: a
   source's own dash is the source's.
3. Stale phrases: strings that must no longer appear (given by the caller).
4. Excluded names: every name in ``people_excluded.security`` must be absent from every client
   file; the one place it may appear is the exclusion note on the People List Summary sheet.

Findings carry counts and IDs, never a name or a passage.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from parity.gate.files import (
    Bundle,
    Office,
    docx_text,
    excel_files,
    is_people_workbook,
    word_files,
    xlsx_strings,
)
from parity.gate.report import CheckResult, Finding, result

NAME = "doc_checks"
_CITED = re.compile(r"\b([SJU]\d{1,3})\b")
_DASHES = re.compile(r"(?: - | – |—|–)")
_BLANK = "X"


def valid_ids(bundle: Bundle) -> set[str]:
    """The IDs a citation may name."""
    return set(bundle.get("sources") or {}) | set(bundle.get("jobs_read") or {})


def orphan_citations(text: str, ids: set[str]) -> list[str]:
    """Cited IDs that are not in ``ids``, sorted by letter then number."""
    cited = set(_CITED.findall(text))
    return sorted(cited - ids, key=lambda s: (s[0], int(s[1:])))


def dash_hits(text: str, quotes: Sequence[str] = ()) -> int:
    """How many banned dashes the text carries once verbatim quotes are blanked."""
    for quote in quotes:
        if quote:
            text = text.replace(quote, _BLANK)
    return len(_DASHES.findall(text))


def excluded_names(bundle: Bundle) -> list[str]:
    """The names of ``people_excluded.security`` (the part before the bracketed title)."""
    out = []
    for entry in (bundle.get("people_excluded") or {}).get("security") or ():
        name = str(entry).split(" (")[0].strip()
        if name:
            out.append(name)
    return out


def _name_pattern(name: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.I)


def _count_phrases(text: str, phrases: Sequence[str]) -> dict[str, int]:
    return {p: len(_name_pattern(p).findall(text)) for p in phrases if p}


def _quotes(bundle: Bundle) -> list[str]:
    extracts = ((bundle.get("prose") or {}).get("evidence") or {}).get("extracts") or ()
    return [str(q) for e in extracts for q in (e.get("quotes") or ())]


def run_doc_checks(
    files: list[Path],
    bundle: Bundle,
    office: Office | None = None,
    stale: Sequence[str] = (),
) -> CheckResult:
    """The four checks on every Word file, plus the excluded-name check on the workbooks."""
    ids = valid_ids(bundle)
    names = excluded_names(bundle)
    quotes = _quotes(bundle)
    findings: list[Finding] = []
    details: dict[str, Any] = {"text_source": {}}
    for path in word_files(files):
        data = path.read_bytes()
        text = office.pdf_text(data) if office is not None else None
        details["text_source"][path.name] = "pdf" if text is not None else "docx"
        if text is None:
            text = docx_text(data)
        orphans = orphan_citations(text, ids)
        if orphans:
            findings.append(
                Finding(NAME, f"orphan citations ({len(orphans)}): {', '.join(orphans)}", path.name)
            )
        dashes = dash_hits(text, quotes)
        if dashes:
            findings.append(Finding(NAME, f"banned dashes: {dashes}", path.name))
        for phrase, n in _count_phrases(text, stale).items():
            if n:
                findings.append(
                    Finding(NAME, f"stale phrase still present ({n}): {phrase}", path.name)
                )
        hits = sum(1 for n in _count_phrases(text, names).values() if n)
        if hits:
            findings.append(Finding(NAME, f"excluded names present: {hits}", path.name))
    for path in excel_files(files):
        hits = 0
        for sheet, _coord, text in xlsx_strings(path):
            if is_people_workbook(path) and sheet == "Summary" and "excluded" in text.casefold():
                continue
            hits += sum(1 for n in _count_phrases(text, names).values() if n)
        if hits:
            findings.append(Finding(NAME, f"excluded names present in cells: {hits}", path.name))
    return result(NAME, findings, details)
