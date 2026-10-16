"""Shared state of one mapping pass: the snapshot, the rules, the registries and the violations.

Mapping is pure: a ``MapContext`` is built from a ``SnapshotContent`` and configuration, every
mapper reads from it and records violations on it, and nothing touches I/O, the clock or the
environment. Every text that will be rendered passes ``rendered`` (sanitised and leak-scanned)
or ``authored`` (also the D-24 prose rules); a quote passes ``quote`` (withheld on a restricted
term, never altered).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from pydantic import BaseModel

from account_intelligence.grading.config import GradingConfig
from exports.citations import SourceIndex
from exports.config import ExportConfig
from exports.content import SnapshotContent
from exports.numbers import NumberRegistry
from exports.policy import (
    Violation,
    authored_violations,
    guarded_names,
    leak_hits,
    quote_withheld,
    restricted_terms,
    sanitise,
)

M = TypeVar("M", bound=BaseModel)


def digest(model: BaseModel) -> str:
    """SHA-256 of a model's canonical JSON (row digests for the change note)."""
    return hashlib.sha256(model.model_dump_json().encode()).hexdigest()


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
    formula_like: int = 0

    def __post_init__(self) -> None:
        self.index = SourceIndex(self.content.sources)
        self.restricted: tuple[str, ...] = restricted_terms(
            [*self.content.forbidden_terms, *guarded_names(self.content.people_names)],
            self.rules.attribution.name,
        )

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

    def rendered(self, text: str | None, where: str) -> str:
        """Sanitise and leak-scan text that will be rendered (B9, B11)."""
        clean = sanitise(text or "")
        for pattern in leak_hits(clean, self.restricted):
            self.fail("leak", where, pattern)
        if clean.startswith(("=", "+", "-", "@", "\t", "\r")):
            self.formula_like += 1
        return clean

    def authored(self, text: str, where: str) -> str:
        """``rendered`` plus the D-24 prose rules (banned dashes, stale phrases, abbreviations,
        partner framings) for authored prose."""
        clean = self.rendered(text, where)
        self.flag(
            authored_violations(
                clean, self.rules, where, is_partner=self.content.is_salesforce_partner
            )
        )
        return clean

    def quote(self, text: str, where: str) -> tuple[str, bool]:
        """A verbatim quote: (the quote, False) or (the withheld note, True). Never altered.
        Partner framings inside a quote also withhold it."""
        clean = sanitise(text)
        restricted = self.restricted
        if self.content.is_salesforce_partner:
            restricted = restricted + self.rules.partner_rule.framings
        if quote_withheld(clean, restricted):
            self.withheld += 1
            return self.rules.quote_handling.withheld_text, True
        if clean.startswith(("=", "+", "-", "@", "\t", "\r")):
            self.formula_like += 1
        return clean, False

    def code_or_fail(
        self, code: str | None, url: str | None, where: str, row_id: str
    ) -> str | None:
        """The registered code of a row: the stored code when the registry knows it, else the
        registry's code for the URL; otherwise a B4 violation and None. Never invented."""
        if not url:
            self.fail("missing_url", where, row_id)
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


def ref(prefix: str, i: int) -> str:
    """Visible row key: ``P-0001`` (1-based, widening past four digits)."""
    return f"{prefix}-{i:04d}"
