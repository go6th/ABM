"""The number registry and the narrative number check (B6). Pure.

Every number a file renders is registered here with the count it came from (computed in code by
the people, jobs or tracker modules, or by the builder from snapshot rows) and the base it names.
Authored text (written earlier by the distill step) may only contain a number that is registered
or that appears in the rows it cites; anything else fails the build. Numbers are compared in a
normal form: digit-group commas removed, a trailing percent sign dropped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from exports.errors import BannedContent
from exports.models import CountedNumber

# A source code (S-0001) is a citation, not a number.
_CODE = re.compile(r"\bS-\d{4,}\b")
# A standalone number: not glued to a letter or underscore on either side (so "Q3", "3rd" and
# "p95" are not numbers), digit groups with commas, an optional decimal part.
_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\w])")


def normal_form(token: str) -> str:
    """``1,200`` -> ``1200``; ``07`` -> ``7``; ``3.50`` -> ``3.5``; ``40%`` -> ``40``."""
    token = token.rstrip("%").replace(",", "")
    whole, _, frac = token.partition(".")
    whole = whole.lstrip("0") or "0"
    frac = frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def numbers_in(text: str) -> tuple[str, ...]:
    """The numbers in ``text`` in normal form, in order of appearance (source codes skipped)."""
    stripped = _CODE.sub(" ", text)
    return tuple(normal_form(m.group(0)) for m in _NUMBER.finditer(stripped))


class NumberRegistry:
    """Rendered numbers by key. A key registered twice must carry the same value and base."""

    def __init__(self) -> None:
        self._items: dict[str, CountedNumber] = {}
        self._sentences: dict[str, str] = {}

    def add_sentence(self, key: str, sentence: str) -> None:
        """Register a count sentence rendered by a domain module (people, jobs, tracker); every
        number in it is then allowed. Re-registering a key with other text raises."""
        existing = self._sentences.get(key)
        if existing is not None and existing != sentence:
            raise BannedContent("a sentence key was registered with two texts", (key,))
        self._sentences[key] = sentence

    def sentences(self) -> tuple[str, ...]:
        """Registered sentences, by key (deterministic)."""
        return tuple(self._sentences[k] for k in sorted(self._sentences))

    def add(self, key: str, value: int, base: str, sentence: str) -> CountedNumber:
        """Register one number; re-registering a key with a different value or base raises."""
        item = CountedNumber(key=key, value=value, base=base, sentence=sentence)
        existing = self._items.get(key)
        if existing is not None and (existing.value, existing.base) != (value, base):
            raise BannedContent("a number key was registered with two values", (key,))
        self._items[key] = item
        return item

    def items(self) -> tuple[CountedNumber, ...]:
        """All registered numbers, sorted by key (deterministic)."""
        return tuple(self._items[k] for k in sorted(self._items))

    def allowed(self) -> frozenset[str]:
        """Normal forms of every registered value and every number in a registered sentence."""
        out: set[str] = set()
        for item in self._items.values():
            out.add(normal_form(str(item.value)))
            out.update(numbers_in(item.sentence))
        for sentence in self._sentences.values():
            out.update(numbers_in(sentence))
        return frozenset(out)


def unregistered(text: str, allowed: frozenset[str], cited_texts: Iterable[str]) -> tuple[str, ...]:
    """Numbers in ``text`` that are neither registered nor present in the cited rows (B6)."""
    cited: set[str] = set()
    for t in cited_texts:
        cited.update(numbers_in(t))
    return tuple(n for n in numbers_in(text) if n not in allowed and n not in cited)
