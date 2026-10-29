"""The reference research's Working tool / User role rule over the platform sentences (pure).

The owner's reference Job Postings file (2026-10-20) classed every posting that was not read by
hand with one rule, measured on the 1,777 such rows with no exception (``docs/parity/
reference_spec.md``, Job Postings): take the platform sentences, a window in the
whitespace-collapsed text that runs from the previous ``.`` or ``*`` to the next ``.``; the class
is Working tool when any cue word of ``config/posting_rubric.yaml heuristic.working_tool_cues``
occurs in them (substring, any case), else User role. Build and Admin and owner roles are never
decided here: only a reading (the extractor, verified by a second pass) confirms those.

The pipeline uses this as the last resort: when the extractor is not asked (caps on and the title
does not look like a Build or Admin and owner role) or does not give a verified class, and for a
posting that was read but whose description names a platform. The result is stored with
``classified_by="heuristic"`` so every export can tell it from a reading. Nothing here calls a
model or the network.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from jobs.match import term_pattern

_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class HeuristicRules:
    """The rule's data (``posting_rubric.yaml heuristic``): the cue words, the window and the
    classes it may answer."""

    working_tool_cues: tuple[str, ...]
    starts_after: tuple[str, ...] = (".", "*")
    ends_at: str = "."
    max_windows_per_platform: int = 4
    max_chars: int = 3000
    working_tool_label: str = "working_tool"
    default_label: str = "user"


@dataclass(frozen=True)
class HeuristicVerdict:
    """The class the rule gives, the window sentences it read and the first one that decided it
    (the sentence that holds a cue; the first window when none does)."""

    label: str
    evidence: str
    windows: tuple[str, ...]


def collapse(text: str) -> str:
    """The text with every run of whitespace as one space (the reference's window text)."""
    return _SPACE.sub(" ", text).strip()


def platform_windows(
    text: str,
    platform: str,
    aliases: tuple[str, ...],
    rules: HeuristicRules,
    *,
    case_sensitive: bool = False,
) -> tuple[str, ...]:
    """The windows around the mentions of one platform in the collapsed ``text``: from just after
    the previous start mark (``.`` or ``*``) to the next ``.`` (included), blanks trimmed; the
    same window once; at most ``max_windows_per_platform``, in text order."""
    flat = collapse(text)
    spans: set[tuple[int, int]] = set()
    for term in (platform, *aliases):
        for m in term_pattern(term, case_sensitive).finditer(flat):
            begin = max(flat.rfind(mark, 0, m.start()) for mark in rules.starts_after) + 1
            stop = flat.find(rules.ends_at, m.end())
            end = len(flat) if stop < 0 else stop + len(rules.ends_at)
            spans.add((begin, end))
    ordered = sorted(spans)[: rules.max_windows_per_platform]
    return tuple(w for w in (flat[b:e].strip() for b, e in ordered) if w)


def classify_heuristic(
    text: str,
    platforms: tuple[str, ...],
    lexicon: Mapping[str, tuple[str, ...]],
    rules: HeuristicRules,
    case_sensitive: frozenset[str] = frozenset(),
) -> HeuristicVerdict:
    """Working tool when the platform windows of ``platforms`` hold a cue word, else User role.
    A posting with no window (the platform is named in the title only) is a User role."""
    windows: list[str] = []
    for platform in platforms:
        windows.extend(
            platform_windows(
                text,
                platform,
                lexicon.get(platform, ()),
                rules,
                case_sensitive=platform in case_sensitive,
            )
        )
    joined = " ".join(windows)[: rules.max_chars].casefold()
    cues = tuple(c.casefold() for c in rules.working_tool_cues)
    decided = next((w for w in windows if any(c in w.casefold() for c in cues)), None)
    if decided is not None and any(c in joined for c in cues):
        return HeuristicVerdict(rules.working_tool_label, decided, tuple(windows))
    # a cue cut off by the character cap does not count: the cap is part of the measured rule
    evidence = windows[0] if windows else ""
    return HeuristicVerdict(rules.default_label, evidence, tuple(windows))
