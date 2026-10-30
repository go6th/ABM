"""The confidentiality scrub over the workspace's text files.

Every text file of ``notes/``, ``data/`` and ``build/`` is copied with the pursuing client's
real name (``meta.pursuing_client_internal``), each of its aliases (``pursuing_client_aliases``,
spellings and email domains) and every email address replaced by the display word
(``meta.pursuing_client_display``), longest term first, any case, on word boundaries. Lines that
read as a table of credits (a Markdown table row naming credits, or a line such as "Credits
used: 1,200") are dropped. Binary files are copied unchanged; the check scans their names.
``residue`` reports what a scrubbed text still carries (the name, an alias, an email address),
by kind only.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from parity.gate.files import Bundle

TEXT_SUFFIXES = frozenset(
    {
        ".md",
        ".txt",
        ".json",
        ".py",
        ".js",
        ".ts",
        ".csv",
        ".tsv",
        ".yaml",
        ".yml",
        ".html",
        ".xml",
        ".log",
        ".ini",
        ".toml",
        ".cfg",
        ".sql",
        ".sh",
    }
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CREDIT_TABLE_ROW = re.compile(r"^\s*\|.*\bcredits?\b", re.I)
_CREDIT_LINE = re.compile(r"^\s*(?:total\s+)?credits?\b.*\d", re.I)


@dataclass
class ScrubSummary:
    """What a scrub pass did."""

    files: int = 0
    text_files: int = 0
    replacements: int = 0
    dropped_lines: int = 0
    binary_copied: list[str] = field(default_factory=list)


def client_terms(bundle: Bundle) -> list[str]:
    """The real name and the aliases, longest first (empty when there is no real name)."""
    meta = bundle.get("meta") or {}
    terms = {str(meta.get("pursuing_client_internal") or "").strip()}
    terms |= {str(a).strip() for a in meta.get("pursuing_client_aliases") or ()}
    return sorted((t for t in terms if t), key=len, reverse=True)


def display_word(bundle: Bundle) -> str:
    """What every file calls the pursuing client."""
    return str((bundle.get("meta") or {}).get("pursuing_client_display") or "Client")


def _term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.I)


def is_credit_line(line: str) -> bool:
    """Whether a line reads as a row of a credit table or a credit balance."""
    return bool(_CREDIT_TABLE_ROW.match(line) or _CREDIT_LINE.match(line))


def scrub_text(text: str, bundle: Bundle) -> tuple[str, int, int]:
    """``(scrubbed text, replacements, dropped lines)``."""
    word = display_word(bundle)
    replaced = 0
    text, n = _EMAIL.subn(word, text)
    replaced += n
    for term in client_terms(bundle):
        text, n = _term_pattern(term).subn(word, text)
        replaced += n
    kept = []
    dropped = 0
    for line in text.splitlines(keepends=True):
        if is_credit_line(line):
            dropped += 1
        else:
            kept.append(line)
    return "".join(kept), replaced, dropped


def is_text_file(path: Path) -> bool:
    """Whether the file is scrubbed as text (by suffix, else by a clean UTF-8 decode)."""
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    try:
        sample = path.read_bytes()[:4096]
    except OSError:
        return False
    if b"\0" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def residue(text: str, bundle: Bundle) -> list[str]:
    """The kinds of confidential term the text still carries: ``email``, ``client_name``."""
    kinds = []
    if _EMAIL.search(text):
        kinds.append("email")
    if any(_term_pattern(t).search(text) for t in client_terms(bundle)):
        kinds.append("client_name")
    return kinds


def walk(root: Path) -> Iterator[Path]:
    """Every file under ``root``, sorted by relative path."""
    return iter(sorted(p for p in root.rglob("*") if p.is_file()))


def scrub_tree(src: Path, dst: Path, bundle: Bundle) -> ScrubSummary:
    """Copy ``src`` to ``dst`` with every text file scrubbed."""
    summary = ScrubSummary()
    for path in walk(src):
        rel = path.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        summary.files += 1
        if is_text_file(path):
            text, replaced, dropped = scrub_text(
                path.read_text(encoding="utf-8", errors="replace"), bundle
            )
            target.write_text(text, encoding="utf-8")
            summary.text_files += 1
            summary.replacements += replaced
            summary.dropped_lines += dropped
        else:
            shutil.copyfile(path, target)
            summary.binary_copied.append(str(rel))
    return summary
