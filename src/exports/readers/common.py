"""Shared state of one mapping pass: the snapshot, the rules, the registries and the violations.

Mapping is pure: a ``MapContext`` is built from a ``SnapshotContent`` and configuration, every
mapper reads from it and records violations on it, and nothing touches I/O, the clock or the
environment. Every text that will be rendered passes ``rendered`` (sanitised and leak-scanned)
or ``authored`` (also the D-24 prose rules); a quote passes ``quote`` (withheld on a restricted
term, never altered).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Generic, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel

from account_intelligence.grading.config import GradingConfig
from exports.citations import SourceIndex
from exports.config import ExportConfig, SkillAttribute, SkillVocabulary, skill_vocabulary
from exports.content import SnapshotContent
from exports.contenturls import clean_urls
from exports.numbers import NumberRegistry
from exports.policy import (
    Violation,
    authored_violations,
    denied_link,
    guarded_names,
    leak_hits,
    quote_withheld,
    restricted_terms,
    sanitise,
)

M = TypeVar("M", bound=BaseModel)

# the longest title a file prints (a posting's or a person's); one over it is cut and counted
TITLE_MAX = 1_000


def digest(model: BaseModel) -> str:
    """SHA-256 of a model's canonical JSON (row digests for the change note)."""
    return hashlib.sha256(model.model_dump_json().encode()).hexdigest()


def restricted_of(content: SnapshotContent, attribution: str) -> tuple[str, ...]:
    """The literal terms no client file built from ``content`` may carry (B9): the snapshot's
    forbidden terms (the other accounts' names, the tenant's excluded companies) and the names
    of people the files do not list. People excluded by title are listed by name with the reason
    on the People List Summary sheet (master prompt v2 9.3), so their names are not guarded;
    every other unlisted name still is. The export build and the parity bundle's render both
    scan their files for exactly this set."""
    counts = content.people_counts
    disclosed = {n.casefold() for n, *_rest in (counts.excluded if counts else ())}
    guarded = [n for n in guarded_names(content.people_names) if n.casefold() not in disclosed]
    return restricted_terms([*content.forbidden_terms, *guarded], attribution)


@dataclass(frozen=True)
class Mapped(Generic[M]):
    """A mapped file: the model (None when mapping failed), its rows and the codes it cites."""

    model: M | None
    row_ids: tuple[str, ...] = ()
    digests: tuple[str, ...] = ()
    refs: tuple[str, ...] = ()
    cited: frozenset[str] = frozenset()


@dataclass
class MapContext:
    """Everything a mapper may read, plus the collectors it writes to."""

    content: SnapshotContent
    cfg: ExportConfig
    grading: GradingConfig
    registry: NumberRegistry = field(default_factory=NumberRegistry)
    violations: list[Violation] = field(default_factory=list)
    withheld: int = 0
    # authored blocks a content rule kept out of a file: (where, row id, rule). Listed in the
    # manifest; the build goes on without them (never the text)
    withheld_blocks: list[tuple[str, str, str]] = field(default_factory=list)
    formula_like: int = 0
    # rendered texts replaced by the withheld note because the leak scan fired on them (B9);
    # counted in the manifest, never the text or the pattern (a guarded term is a person's name)
    leaks_withheld: int = 0
    # the research skill's vocabulary: attribute tiers and meanings, the news window
    skill: SkillVocabulary = field(default_factory=skill_vocabulary)
    # ids of rows whose stored address was unusable (set by ``clean_urls``)
    bad_url_rows: frozenset[str] = frozenset()
    # titles printed cut at ``TITLE_MAX`` characters (counted in the manifest, never silent)
    titles_cut: int = 0

    def attribute(self, key: str) -> SkillAttribute | None:
        """The spec of a stored attribute key, or None outside the vocabulary."""
        return self.skill.attribute(key)

    def __post_init__(self) -> None:
        # every address repaired or removed before any mapper reads it (a bad link withholds
        # its row; it never reaches a render model as a validation error)
        self.content, dropped, self.bad_url_rows = clean_urls(self.content)
        self.withheld_blocks.extend(dropped)
        self.index = SourceIndex(self.content.sources)
        # registered sources whose link names a data tool (a vendor's public company page): a
        # client file may not link them, so every row that rests on them is withheld (staging
        # 2026-10-25: two such links failed the third-party scan of a finished research)
        self.denied_codes: frozenset[str] = frozenset(
            s.code for s in self.content.sources if denied_link(s.url)
        )
        # people excluded by title are listed by name with the reason on the People List Summary
        # sheet (master prompt v2 9.3), so their names are not guarded; every other unlisted
        # name still is
        self.restricted: tuple[str, ...] = restricted_of(self.content, self.rules.attribution.name)
        # out-of-scope platform names (scope.yaml never_report) are masked in every rendered
        # text and quote, the replacements counted in ``masked_terms``
        self.masked_terms = 0
        self._masks = tuple(
            re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
            for name in self.cfg.scope.never_report
        )

    def mask(self, text: str) -> str:
        """``text`` with every never_report platform name replaced by the scope mask (whole
        word, any case), the replacements counted."""
        for pattern in self._masks:
            text, n = pattern.subn(self.cfg.scope.mask, text)
            self.masked_terms += n
        return text

    @property
    def rules(self):  # type: ignore[no-untyped-def]
        """The content rules (shortcut)."""
        return self.cfg.rules

    def flag(self, violations: Iterable[Violation]) -> None:
        """Record violations."""
        self.violations.extend(violations)

    def fail(self, code: str, where: str, item: str) -> None:
        """Record one violation."""
        self.violations.append(Violation(code, where, item))

    def leaks(self, text: str) -> bool:
        """True when the leak scan fires on ``text`` (B9): the denylist, the run's restricted
        terms and guarded names, provider codes or an internal hash."""
        return bool(leak_hits(sanitise(text), self.restricted))

    def rendered(self, text: str | None, where: str) -> str:
        """Sanitise, mask out-of-scope platforms and leak-scan text that will be rendered
        (B9, B11). A text the scan fires on is replaced by the withheld note and counted, and the
        set goes on: two leaked sentences among 1,363 findings held back all five files of a
        finished research (staging 2026-10-25, ``build_failed:leak``). The stored files are
        still scanned byte for byte before they are kept, and a hit there fails the build."""
        clean = self.mask(sanitise(text or ""))
        if leak_hits(clean, self.restricted):
            self.leaks_withheld += 1
            withheld: str = self.cfg.rules.quote_handling.withheld_text
            return withheld
        if clean.startswith(("=", "+", "-", "@", "\t", "\r")):
            self.formula_like += 1
        return clean

    def titled(self, text: str | None, where: str) -> str:
        """``rendered`` for a title the file prints as it is (a posting's, a person's): the
        reference files hold no dash as punctuation, so a spaced dash or hyphen becomes a comma
        and a lone en or em dash a hyphen (the rule the website's table download applies). Only
        the printed copy changes, never the stored title. A title longer than ``TITLE_MAX``
        characters is cut with an ellipsis and counted (``titles_cut``): no real title is that
        long, and a 40,000 character one used to stop all five files at the Word cell limit."""
        return self.short(dashless(self.rendered(text, where)))

    def short(self, text: str) -> str:
        """``text`` cut at ``TITLE_MAX`` characters with an ellipsis, the cut counted
        (``titles_cut``). Applied where a name or title is also a row's key or must fit a Word
        table cell: the file and the row keys then agree."""
        if len(text) <= TITLE_MAX:
            return text
        self.titles_cut += 1
        return text[: TITLE_MAX - 1].rstrip() + "\u2026"

    def prose_violations(self, text: str, where: str) -> tuple[Violation, ...]:
        """The D-24 prose rules (banned dashes, stale phrases, abbreviations, partner framings)
        on authored prose. The caller withholds the block on a violation; the rules never fail
        the build on their own (one dashed sentence held back five files, staging 2026-10-21)."""
        return authored_violations(
            sanitise(text), self.rules, where, is_partner=self.content.is_salesforce_partner
        )

    def withhold(self, where: str, row_id: str, rule: str) -> None:
        """Leave one authored block out of its file for ``rule``, counted and listed."""
        self.withheld_blocks.append((where, row_id, rule))

    def quote(self, text: str, where: str) -> tuple[str, bool]:
        """A verbatim quote: (the quote, False) or (the withheld note, True). Never altered
        except for the scope mask over an out-of-scope platform name (master prompt v2 section
        0). Partner framings inside a quote also withhold it."""
        clean = self.mask(sanitise(text))
        restricted = self.restricted
        if self.content.is_salesforce_partner:
            restricted = restricted + self.rules.partner_rule.framings
        if quote_withheld(clean, restricted):
            self.withheld += 1
            return self.rules.quote_handling.withheld_text, True
        if clean.startswith(("=", "+", "-", "@", "\t", "\r")):
            self.formula_like += 1
        return clean, False

    def denied(self, code: str | None, url: str | None) -> bool:
        """True when a row's link, or the source it cites, names a data tool."""
        return bool(code and code in self.denied_codes) or bool(url and denied_link(url))

    def code_or_fail(
        self, code: str | None, url: str | None, where: str, row_id: str
    ) -> str | None:
        """The registered code of a row: the stored code when the registry knows it, else the
        registry's code for the URL; otherwise a B4 violation and None. Never invented."""
        if not url:
            self.fail("bad_url" if row_id in self.bad_url_rows else "missing_url", where, row_id)
            return None
        if code is not None:
            if self.index.knows(code):
                return code
            self.fail("unknown_source_code", where, code)
            return None
        found = self.index.code_for(url)
        if found is None:
            self.fail("missing_source_code", where, row_id)
        return found

    def code_or_withhold(
        self, code: str | None, url: str | None, where: str, row_id: str
    ) -> str | None:
        """``code_or_fail`` for a data row (a posting, a detection, a person): a row with no
        public link, or a link the registry never saw, is withheld from its file and listed in
        the manifest instead of failing the whole set (staging 2026-10-24: one detection whose
        only evidence was a feed record held back all five files). A stored code the registry
        does not know is still a failure (corrupt provenance, never withheld quietly)."""
        if not url:
            self.withhold(
                where, row_id, "bad_url" if row_id in self.bad_url_rows else "missing_url"
            )
            return None
        if self.denied(code, url):
            self.withhold(where, row_id, "tool_link")
            return None
        if code is not None:
            if self.index.knows(code):
                return code
            self.fail("unknown_source_code", where, code)
            return None
        found = self.index.code_for(url)
        if found is None:
            self.withhold(where, row_id, "missing_source_code")
        return found

    def withheld_in(self, where: str) -> int:
        """How many rows ``where`` has withheld so far (row conservation counts them)."""
        return sum(1 for w, _row, _rule in self.withheld_blocks if w == where)

    def printable(self, text: str | None, where: str, row_id: str) -> bool:
        """True when ``text`` keeps a printable character once sanitised; otherwise a
        ``text_missing`` violation (a required text is never replaced by an internal id)."""
        if sanitise(text or "").strip():
            return True
        self.fail("text_missing", where, row_id)
        return False

    def capped(self, n: int, cap: int, where: str) -> bool:
        """True (and a ``row_cap`` violation) when ``n`` rows exceed the cap (B3)."""
        if n > cap:
            self.fail("row_cap", where, f"{n}>{cap}")
            return True
        return False


# a dash used as punctuation: a figure dash, en dash, em dash or horizontal bar, or one or two
# hyphens, between words (the website's table download applies the same two steps)
_DASHES = "\u2012\u2013\u2014\u2015"
_SPACED_DASH = re.compile(f"(?<=\\S)\\s+(?:[{_DASHES}]|-{{1,2}})\\s+(?=\\S)")
_ANY_DASH = re.compile(f"[{_DASHES}]")


def dashless(text: str) -> str:
    """``text`` without a dash as punctuation: " - " between words becomes ", ", any other dash
    character becomes a hyphen."""
    return _ANY_DASH.sub("-", _SPACED_DASH.sub(", ", text))


def ref(prefix: str, i: int) -> str:
    """Visible row key: ``P-0001`` (1-based, widening past four digits)."""
    return f"{prefix}-{i:04d}"


def parse_event_date(text: str | None) -> date | None:
    """A stored event date (``2026-03-15``, ``2026-03`` or ``2026``; a month or a year is its
    first day) as a date; anything else is undated. A finding is dated by when the thing
    happened, never by when it was found."""
    if not text:
        return None
    parts = text.strip()[:10].split("-")
    if len(parts[0]) != 4:
        return None
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 and parts[1] else 1
        day = int(parts[2]) if len(parts) > 2 and parts[2] else 1
        return date(year, month, day)
    except ValueError:
        return None


def months_before(day: date, months: int) -> date:
    """The same day of the month ``months`` earlier (clamped to the 28th when that day does not
    exist): the start of a window such as the 18 months of news."""
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    try:
        return day.replace(year=year, month=month + 1)
    except ValueError:
        return day.replace(year=year, month=month + 1, day=28)


def host_of(url: str) -> str:
    """The host of a URL (the domain column), or the URL itself when it has none."""
    return urlparse(url).hostname or url
