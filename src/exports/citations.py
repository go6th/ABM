"""Source-code resolution and orphan detection (B4, B5). Pure.

Codes come only from the account's source registry (``sources.source_code``, assigned once and
never renumbered); the builders never mint one. A row whose URL has no registered code is
rejected (B4); a code cited anywhere must be listed in the Evidence file (B5).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from account_intelligence.urls import normalize_url
from exports.content import SourceRow
from exports.policy import Violation


class SourceIndex:
    """Registry lookups by code and by normalised URL (the registry's own dedupe key)."""

    def __init__(self, sources: Iterable[SourceRow]) -> None:
        self.by_code: dict[str, SourceRow] = {}
        self.by_url: dict[str, SourceRow] = {}
        for s in sources:
            self.by_code[s.code] = s
            self.by_url[_norm(s.url)] = s

    def code_for(self, url: str | None) -> str | None:
        """The registered code of ``url``, or None (never invented)."""
        if not url:
            return None
        found = self.by_url.get(_norm(url))
        return found.code if found else None

    def knows(self, code: str) -> bool:
        """True when ``code`` is in the registry."""
        return code in self.by_code


def _norm(url: str) -> str:
    try:
        return normalize_url(url)
    except ValueError:
        return url


def unknown_codes(cited: Iterable[str], index: SourceIndex, where: str) -> tuple[Violation, ...]:
    """Codes cited that the registry does not hold (an invented or renumbered code)."""
    return tuple(
        Violation("unknown_source_code", where, c) for c in sorted(set(cited)) if not index.knows(c)
    )


def orphans(
    cited_by_file: Mapping[str, Iterable[str]], in_evidence: Iterable[str]
) -> tuple[Violation, ...]:
    """Codes cited by a file that the Evidence file does not list (B5)."""
    listed = set(in_evidence)
    return tuple(
        Violation("orphan_citation", kind, code)
        for kind in sorted(cited_by_file)
        for code in sorted(set(cited_by_file[kind]))
        if code not in listed
    )
