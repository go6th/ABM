"""Fast negative answers for the client-output scan (fix wave C, 2026-10-29, scale).

The leak scan runs a handful of denylist patterns and one pattern per guarded name over every
cell, file and archive member of a build; at 7,000 postings that was a third of the build time.
Nearly every text holds none of them, so a cheap test that proves "this pattern cannot match this
text" lets the scan skip the regular expression. The test only ever answers "cannot match" (so a
hit is never missed); when it cannot prove that, the pattern runs as before.

* ``needles(pattern)``: literal substrings of which at least one must appear in every text the
  pattern matches, read from the parsed pattern (the longest run of literal characters of its top
  level sequence, or one per alternative of a top level alternation). None when there is none.
* ``fold(text)``: the text as a case-insensitive pattern sees it, for ASCII needles: lower case,
  with the characters the regular expression engine counts equal to an ASCII letter folded to
  it (dotless and dotted i, long s, the Kelvin sign) so an obfuscated spelling is still found.
* ``TermIndex``: literal terms (the guarded names) found by their words: a term can only match a
  text that holds every one of its words, so one pass over the text's words picks the few terms
  worth a regular expression, however many terms there are.

``tests/safety/test_prefilter.py`` compares the filtered scan with the plain one on adversarial
texts and checks the folding against the engine's own case tables for every code point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

try:  # the parser module of the standard library (private since 3.11, stable in 3.11 to 3.13)
    from re import _constants as _c  # type: ignore[attr-defined]
    from re import _parser as _p  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - a Python without it simply never skips a pattern
    _p = None
    _c = None

MIN_NEEDLE = 3
_WORD = re.compile(r"\w+")
_NON_WORD = re.compile(r"\W")


def fold(text: str) -> str:
    """``text`` folded for the comparison with an ASCII needle (see the module docstring)."""
    if text.isascii():
        return text.lower()
    return text.replace("İ", "i").replace("ı", "i").casefold()


def _runs(parsed: object) -> list[str]:
    """The literal runs of one sequence, in order. Zero-width items (word boundaries, look
    arounds) neither break nor extend a run; anything else breaks it."""
    runs: list[str] = []
    cur: list[str] = []
    assert _c is not None
    for op, arg in parsed:  # type: ignore[attr-defined]
        if op == _c.LITERAL:
            cur.append(chr(arg))
        elif op in (_c.AT, _c.ASSERT, _c.ASSERT_NOT):
            continue
        else:
            if cur:
                runs.append("".join(cur))
                cur = []
    if cur:
        runs.append("".join(cur))
    return runs


def _best(parsed: object, ignore_case: bool) -> str | None:
    runs = [r for r in _runs(parsed) if r.isascii() and len(r) >= MIN_NEEDLE]
    if not runs:
        return None
    best = max(runs, key=len)
    return best.lower() if ignore_case else best


@lru_cache(maxsize=512)
def needles(pattern: re.Pattern[str]) -> tuple[str, ...] | None:
    """Substrings of which at least one appears in every text ``pattern`` matches (lower case
    when the pattern ignores case), or None when none can be shown."""
    if _p is None or _c is None:  # pragma: no cover
        return None
    ignore_case = bool(pattern.flags & re.IGNORECASE)
    try:
        parsed = _p.parse(pattern.pattern, pattern.flags)
    except Exception:  # noqa: BLE001 - a pattern the parser will not read is always run
        return None
    items = list(parsed)
    if len(items) == 1 and items[0][0] == _c.BRANCH:
        found: list[str] = []
        for branch in items[0][1][1]:
            one = _best(branch, ignore_case)
            if one is None:
                return None
            found.append(one)
        return tuple(dict.fromkeys(found))
    one = _best(parsed, ignore_case)
    return None if one is None else (one,)


# A text longer than this is folded and tokenised in slices (a document body of 140 MB would
# otherwise need a folded copy and a list of every word at once: over a gigabyte at 7,000
# postings). The test stays exact: a needle is looked for with an overlap of its own length, and a
# slice always ends where a word ends.
SLICE = 4_000_000


class Scanned:
    """One text prepared for the pattern tests: folded once (small texts) or slice by slice
    (large ones)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self._folded: str | None = None

    def folded(self) -> str:
        """The whole text folded (only for a text that fits in one slice)."""
        if self._folded is None:
            self._folded = fold(self.text)
        return self._folded

    def _contains(self, found: tuple[str, ...], ignore_case: bool) -> bool:
        text = self.text
        if len(text) <= SLICE:
            hay = self.folded() if ignore_case else text
            return any(n in hay for n in found)
        reach = max(len(n) for n in found)
        for start in range(0, len(text), SLICE):
            part = text[start : start + SLICE + reach]
            hay = fold(part) if ignore_case else part
            if any(n in hay for n in found):
                return True
        return False

    def cannot_match(self, pattern: re.Pattern[str]) -> bool:
        """True when ``pattern`` provably cannot match the text."""
        found = needles(pattern)
        if found is None:
            return False
        return not self._contains(found, bool(pattern.flags & re.IGNORECASE))

    def words(self) -> set[str]:
        """The folded words of the text (runs of word characters), a slice at a time."""
        if len(self.text) <= SLICE:
            return set(_WORD.findall(self.folded()))
        out: set[str] = set()
        text = self.text
        start = 0
        # LOOP(word slices): cap=ceil(len(text) / SLICE) slices (each pass moves start forward by
        # at least one character); exit=start reaches the end of the text
        while start < len(text):
            end = min(len(text), start + SLICE)
            if end < len(text):  # end the slice where a word ends
                gap = _NON_WORD.search(text, end)
                end = gap.start() if gap else len(text)
            out.update(_WORD.findall(fold(text[start:end])))
            start = end
        return out


def cannot_match(pattern: re.Pattern[str], text: str) -> bool:
    """True when ``pattern`` provably cannot match ``text``."""
    return Scanned(text).cannot_match(pattern)


@dataclass(frozen=True)
class TermIndex:
    """Literal terms (compiled word-bounded, any case) found by their words."""

    patterns: tuple[re.Pattern[str], ...]
    # word -> indices of the terms whose rarest word it is (every word of a term must be in the
    # text for the term to match; the term is checked when its key word is)
    by_word: dict[str, tuple[int, ...]]
    words: tuple[tuple[str, ...], ...]
    # terms the word test cannot judge (no ASCII word): always run
    always: tuple[int, ...]

    def candidates(self, scanned: Scanned) -> list[int]:
        """Indices (ascending) of the terms that can match the text."""
        if not self.by_word and not self.always:
            return []
        picked: set[int] = set(self.always)
        if self.by_word:
            present = scanned.words()
            for word in present.intersection(self.by_word):
                for i in self.by_word[word]:
                    if all(w in present for w in self.words[i]):
                        picked.add(i)
        return sorted(picked)


def build_index(terms: tuple[str, ...]) -> TermIndex:
    """The index of a term set; ``patterns[i]`` is the pattern of the i-th non blank term."""
    patterns: list[re.Pattern[str]] = []
    by_word: dict[str, list[int]] = {}
    words: list[tuple[str, ...]] = []
    always: list[int] = []
    for term in terms:
        stripped = term.strip()
        if not stripped:
            continue
        i = len(patterns)
        patterns.append(re.compile(r"(?<!\w)" + re.escape(stripped) + r"(?!\w)", re.I))
        mine = tuple(_WORD.findall(stripped)) if stripped.isascii() else ()
        mine = tuple(w.lower() for w in mine)
        words.append(mine)
        if not mine:
            always.append(i)
            continue
        rarest = max(mine, key=len)
        by_word.setdefault(rarest, []).append(i)
    return TermIndex(
        tuple(patterns),
        {w: tuple(ix) for w, ix in by_word.items()},
        tuple(words),
        tuple(always),
    )
