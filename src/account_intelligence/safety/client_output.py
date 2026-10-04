"""Client-output scanner used at runtime on every export (W7) and by the tracker's detail scrub.

Patterns come from ``config/client_output_denylist.txt``, a copy of the skill's
``assets/client_output_denylist.txt`` (a test keeps them identical) so the runtime image does not
need the skill folder. ``extra_terms`` are literal strings added per call: tenant names, other
accounts' names, client staff names and internal source ids from the redaction map.

This is the same rule set as ``scripts/scan_client_output.py`` (the CI and operator tool), which
also opens docx, xlsx, pptx and zip members; this module scans text the service is about to emit.
GUARANTEE(test_export_and_count_sentence_are_client_safe)
GUARANTEE(test_client_view_is_allow_listed_scanned_and_ordered)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from account_intelligence.config import CONFIG_DIR


class ClientOutputLeak(ValueError):
    """Text bound for a client contains a forbidden term. Carries the pattern, never the text."""

    def __init__(self, hits: tuple[Hit, ...]):
        self.hits = hits
        super().__init__(f"client output blocked: {len(hits)} forbidden term(s)")


@dataclass(frozen=True)
class Hit:
    """One match: the pattern that fired and where (offset), not the matched text."""

    pattern: str
    start: int


@lru_cache(maxsize=2)
def load_patterns(path: Path | None = None) -> tuple[re.Pattern[str], ...]:
    """Compile the denylist (one case-insensitive regex per line, ``#`` comments)."""
    lines = (path or CONFIG_DIR / "client_output_denylist.txt").read_text().splitlines()
    return tuple(re.compile(x.strip(), re.I) for x in lines if x.strip() and not x.startswith("#"))


@lru_cache(maxsize=64)
def _term_patterns(terms: tuple[str, ...]) -> tuple[re.Pattern[str], ...]:
    """Literal terms as word-bounded, case-insensitive patterns (compiled once per term set)."""
    return tuple(
        re.compile(r"(?<!\w)" + re.escape(t.strip()) + r"(?!\w)", re.I) for t in terms if t.strip()
    )


def scan_text(text: str, extra_terms: Iterable[str] = ()) -> tuple[Hit, ...]:
    """All hits in ``text``; literal ``extra_terms`` match on word boundaries, any case."""
    patterns = load_patterns() + _term_patterns(tuple(t for t in extra_terms if t))
    return tuple(Hit(p.pattern, m.start()) for p in patterns for m in p.finditer(text))


def assert_client_safe(texts: Iterable[str], extra_terms: Iterable[str] = ()) -> None:
    """Raise ``ClientOutputLeak`` if any text would leak; call before every client export."""
    terms = tuple(extra_terms)
    hits = tuple(h for text in texts for h in scan_text(text, terms))
    if hits:
        raise ClientOutputLeak(hits)
