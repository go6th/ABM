"""Wording lint (AL5, CW5, D-76): rejects outreach-style phrasing in every rendered alert, digest,
Slack message, email, webhook body and CRM payload. The fixed preparation label is the one
sanctioned mention of an "opening line". Pure.

GUARANTEE(test_the_wording_lint_rejects_outreach_phrasing)
"""

from __future__ import annotations

import re

from alerts.sensitive import LABEL

_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\breach(?:ing)? out\b",
        r"\bcongratulat",
        r"\bgreat (?:time|moment|opportunity|opening) to\b",
        r"\bperfect (?:time|timing|moment)\b",
        r"\bnow is the (?:time|moment)\b",
        r"\bopportunity to (?:discuss|connect|help|engage|pitch|sell)\b",
        r"\buse (?:this|it) (?:to|as)\b",
        r"\b(?:opening|conversation|ice[- ]?breaker) (?:line|starter)s?\b",
        r"\btalking points?\b",
        r"\bhook\b",
        r"\bpitch\b",
        r"\bleverage (?:this|the|their)\b",
        r"\bsorry to hear\b",
        r"\bhelp (?:them|you) recover\b",
        r"\bsend (?:an? )?(?:email|message|note) to\b",
        r"\bbook (?:a|the) (?:meeting|call)\b",
    )
)


class WordingRefused(ValueError):
    """Text that suggests outreach."""


def problems(text: str) -> list[str]:
    """The outreach-style phrases found (the fixed preparation label excluded)."""
    cleaned = text.replace(LABEL, " ")
    return [m.group(0) for p in _PATTERNS for m in p.finditer(cleaned)]


def check(text: str) -> str:
    """``text`` unchanged, or WordingRefused."""
    found = problems(text)
    if found:
        raise WordingRefused(f"outreach-style wording: {sorted(set(found))}")
    return text
