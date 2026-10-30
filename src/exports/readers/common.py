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
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Generic, TypeVar
from urllib.parse import unquote_plus, urlparse

from pydantic import BaseModel

from account_intelligence.grading.config import GradingConfig
from account_intelligence.safety.client_output import scan_text
from exports.citations import SourceIndex
from exports.config import ExportConfig, SkillAttribute, SkillVocabulary, skill_vocabulary
from exports.content import SnapshotContent
from exports.contenturls import clean_urls
from exports.numbers import NumberRegistry
from exports.policy import (
    Violation,
    authored_violations,
    client_rule,
    guarded_names,
    leak_hits,
    leak_rule,
    restricted_terms,
    sanitise,
)
from exports.verify import third_party_word

M = TypeVar("M", bound=BaseModel)

# the longest title a file prints (a posting's or a person's); one over it is cut and counted
TITLE_MAX = 1_000


def digest(model: BaseModel) -> str:
    """SHA-256 of a model's canonical JSON (row digests for the change note)."""
    return hashlib.sha256(model.model_dump_json().encode()).hexdigest()


def own_names(content: SnapshotContent) -> tuple[str, ...]:
    """The names the account's own files carry by definition: its name, domain and careers
    host, the white-label brand, and what the stored entity lock knows of the company (legal
    name, brand, domains, subsidiaries). A restricted term that is one of them, or a whole word
    inside one, is dropped from the restricted set (``policy.names_own``): another account of the
    client with the same domain or name, or a name that is part of this one, would otherwise put
    its "forbidden" term in the title of every file."""
    lock = content.entity_lock or {}
    found: list[object] = [
        content.account_name,
        content.domain,
        content.careers_host,
        content.brand_name,
        lock.get("legal_name"),
        lock.get("brand"),
        lock.get("primary_domain"),
        *(lock.get("allowed_domains") or ()),
        *(lock.get("careers_hosts") or ()),
    ]
    for sub in lock.get("subsidiaries") or ():
        if isinstance(sub, dict):
            found += [sub.get("name"), sub.get("domain")]
    names = [str(x).strip() for x in found if isinstance(x, str) and x.strip()]
    return tuple(dict.fromkeys(names))


def restricted_of(content: SnapshotContent, attribution: str) -> tuple[str, ...]:
    """The literal terms no client file built from ``content`` may carry (B9): the snapshot's
    forbidden terms (the other accounts' names, the tenant's excluded companies) and the names
    of people the files do not list, less the account's own identity (``own_names``). People
    excluded by title are listed by name with the reason on the People List Summary sheet
    (master prompt v2 9.3), so their names are not guarded; every other unlisted name still is.
    The export build and the parity bundle's render both scan their files for exactly this
    set."""
    counts = content.people_counts
    disclosed = {n.casefold() for n, *_rest in (counts.excluded if counts else ())}
    guarded = [n for n in guarded_names(content.people_names) if n.casefold() not in disclosed]
    return restricted_terms([*content.forbidden_terms, *guarded], attribution, own_names(content))


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
    # the same texts by (where, rule): which part of which file and which client-output rule held
    # them (neutral names, never the text or the term), for the manifest and the log
    texts_withheld: Counter[tuple[str, str]] = field(default_factory=Counter)
    # the research skill's vocabulary: attribute tiers and meanings, the news window
    skill: SkillVocabulary = field(default_factory=skill_vocabulary)
    # ids of rows whose stored address was unusable (set by ``clean_urls``)
    bad_url_rows: frozenset[str] = frozenset()
    # titles printed cut at ``TITLE_MAX`` characters (counted in the manifest, never silent)
    titles_cut: int = 0
    # ``(stored name, printed name)`` of an account whose own name a client file may not print
    # (``ExportBuilder._client_name``): authored text that names the account says the printed one
    alias: tuple[str, str] | None = None

    def attribute(self, key: str) -> SkillAttribute | None:
        """The spec of a stored attribute key, or None outside the vocabulary."""
        return self.skill.attribute(key)

    def __post_init__(self) -> None:
        # every address repaired or removed before any mapper reads it (a bad link withholds
        # its row; it never reaches a render model as a validation error)
        self.content, dropped, self.bad_url_rows = clean_urls(self.content)
        self.withheld_blocks.extend(dropped)
        self.index = SourceIndex(self.content.sources)
        # people excluded by title are listed by name with the reason on the People List Summary
        # sheet (master prompt v2 9.3), so their names are not guarded; every other unlisted
        # name still is
        self.restricted: tuple[str, ...] = restricted_of(self.content, self.rules.attribution.name)
        self._link_rules: dict[str, str | None] = {}
        self._held: set[tuple[str, str]] = set()
        # registered sources whose link a client file may not carry: it names a data tool (a
        # vendor's public company page), the cost word, or another account or a person the
        # files do not list (a page on another account's site). Every row that rests on one is
        # withheld and listed with the rule (staging 2026-10-25: two such links failed the
        # third-party scan of a finished research; 2026-10-31: the final scan refused a link
        # that named a restricted term)
        self.denied_rules: dict[str, str] = {
            s.code: rule for s in self.content.sources if (rule := self.link_rule(s.url))
        }
        self.denied_codes: frozenset[str] = frozenset(self.denied_rules)
        # out-of-scope platform names (scope.yaml never_report) are masked in every rendered
        # text and quote, the replacements counted in ``masked_terms``
        self.masked_terms = 0
        self._masks = tuple(
            re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
            for name in self.cfg.scope.never_report
        )

    def aliased(self, text: str) -> str:
        """``text`` with the account's stored name replaced by the name the files print, when the
        two differ (whole words, any case): authored text names the account as it was entered."""
        if self.alias is None:
            return text
        stored, printed = self.alias
        return re.sub(rf"(?<!\w){re.escape(stored)}(?!\w)", lambda _m: printed, text, flags=re.I)

    def mask(self, text: str) -> str:
        """``text`` with every never_report platform name replaced by the scope mask (whole
        word, any case), the replacements counted."""
        text = self.aliased(text)
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

    def block_rule(self, text: str, extra: tuple[str, ...] = ()) -> str | None:
        """The client-output rule ``text`` breaks, or None (B9): ``leak`` (a restricted term or
        a person the files do not list), ``tool_name`` (the denylist), ``internal_id`` (a
        provider code or a 64 digit hash), ``cost_word``, ``data_tool_host`` (the third-party
        scan's word and host) or the application guard's own reasons (``provider_name``,
        ``guarded_name``, ``third_party_domain``, ``agents.naming.client_rule``). One function for
        every text a file prints, so the final scans of the produced bytes find nothing left to
        refuse. Names the rule, never the text."""
        hits = leak_hits(text, (*self.restricted, *extra))
        if hits:
            rules = {leak_rule(h) for h in hits}
            return next(r for r in ("leak", "tool_name", "internal_id") if r in rules)
        return third_party_word(text) or client_rule(text)

    def leaks(self, text: str) -> bool:
        """True when ``block_rule`` fires on ``text`` (B9)."""
        return self.block_rule(sanitise(text)) is not None

    def link_rule(self, url: str) -> str | None:
        """The rule that keeps a link out of a client file, or None: ``tool_link`` (it names a
        data tool, its host, or a provider), ``cost_word_link`` (the word the providers use for
        their own unit, which no client file prints) or
        ``restricted_link`` (it names another account, an excluded company or a person the files
        do not list). Judged on the address as written and percent-decoded; the address of an
        archived copy carries the original one, so it follows the row's own."""
        if url in self._link_rules:
            return self._link_rules[url]
        rule: str | None = None
        for seen in dict.fromkeys((url, unquote_plus(unquote_plus(url)))):
            if (
                scan_text(seen)
                or client_rule(seen) is not None
                or third_party_word(seen) == "data_tool_host"
            ):
                rule = "tool_link"
            elif third_party_word(seen) == "cost_word":
                rule = "cost_word_link"
            elif scan_text(seen, self.restricted):
                rule = "restricted_link"
            if rule:
                break
        self._link_rules[url] = rule
        return rule

    def note_withheld(self, where: str, rule: str) -> None:
        """Count one text replaced by the withheld note (``texts_withheld``, ``leaks_withheld``)."""
        self.leaks_withheld += 1
        self.texts_withheld[(where, rule)] += 1

    def rendered(self, text: str | None, where: str) -> str:
        """Sanitise, mask out-of-scope platforms and client-scan text that will be rendered
        (B9, B11, ``block_rule``). A text a rule fires on is replaced by the withheld note and
        counted under its file part and rule, and the set goes on: two leaked sentences among
        1,363 findings held back all five files of a finished research (staging 2026-10-25,
        ``build_failed:leak``). The stored files are still scanned byte for byte before they are
        kept; a hit there withholds the text or the file (``builder``), not the research."""
        clean = self.mask(sanitise(text or ""))
        rule = self.block_rule(clean)
        if rule is not None:
            self.note_withheld(where, rule)
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
        """Leave one authored block or row out of its file for ``rule``, counted and listed. A
        row is withheld once: the first rule that refused it is the one listed."""
        if (where, row_id) in self._held:
            return
        self._held.add((where, row_id))
        self.withheld_blocks.append((where, row_id, rule))

    def quote(self, text: str, where: str) -> tuple[str, bool]:
        """A verbatim quote: (the quote, False) or (the withheld note, True). Never altered
        except for the scope mask over an out-of-scope platform name (master prompt v2 section
        0). Partner framings inside a quote also withhold it."""
        clean = self.mask(sanitise(text))
        extra = self.rules.partner_rule.framings if self.content.is_salesforce_partner else ()
        rule = self.block_rule(clean, tuple(extra))
        if rule is not None:
            self.withheld += 1
            self.texts_withheld[(where, rule)] += 1
            return self.rules.quote_handling.withheld_text, True
        if clean.startswith(("=", "+", "-", "@", "\t", "\r")):
            self.formula_like += 1
        return clean, False

    def denial(self, code: str | None, url: str | None) -> str | None:
        """The rule that keeps a row's link, or the source it cites, out of a client file."""
        if code and code in self.denied_rules:
            return self.denied_rules[code]
        return self.link_rule(url) if url else None

    def denied(self, code: str | None, url: str | None) -> bool:
        """True when a row's link, or the source it cites, may not be in a client file."""
        return self.denial(code, url) is not None

    def resolve_code(
        self, code: str | None, url: str | None, row_id: str
    ) -> tuple[str | None, str | None]:
        """``(code, None)`` or ``(None, rule)``: the registered code of a row (the stored code
        when the registry knows it, else the registry's code for the link) or the rule that
        refuses it (B4): ``missing_url`` or ``bad_url`` (no usable link), ``tool_link``,
        ``cost_word_link`` or ``restricted_link`` (a link a client file may not carry),
        ``unknown_source_code`` (a stored code the registry does not know) or
        ``missing_source_code`` (a link the registry never saw). Never invented."""
        if not url:
            return None, "bad_url" if row_id in self.bad_url_rows else "missing_url"
        if rule := self.denial(code, url):
            return None, rule
        if code is not None:
            return (code, None) if self.index.knows(code) else (None, "unknown_source_code")
        found = self.index.code_for(url)
        return (found, None) if found is not None else (None, "missing_source_code")

    def code_or_withhold(
        self, code: str | None, url: str | None, where: str, row_id: str
    ) -> str | None:
        """The registered code of a data row (a posting, a detection, a person, a finding), or
        None with the row withheld from its file and listed in the manifest under the rule that
        refused it (``resolve_code``) instead of failing the whole set (staging 2026-10-24: one
        detection whose only evidence was a feed record held back all five files; 2026-10-31
        the same for a stored code the registry does not know, which is listed, never quiet)."""
        found, rule = self.resolve_code(code, url, row_id)
        if rule is not None:
            self.withhold(where, row_id, rule)
        return found

    def withheld_in(self, where: str) -> int:
        """How many rows ``where`` has withheld so far (row conservation counts them)."""
        return sum(1 for w, _row, _rule in self.withheld_blocks if w == where)

    def printable(self, text: str | None, where: str, row_id: str) -> bool:
        """True when ``text`` keeps a printable character once sanitised; otherwise the row is
        withheld and listed as ``text_missing`` (a required text is never replaced by an internal
        id, and one blank cell no longer stops the set)."""
        if sanitise(text or "").strip():
            return True
        self.withhold(where, row_id, "text_missing")
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
