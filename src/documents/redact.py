"""Per-tenant redaction before anything is chunked, embedded, indexed or shown to a model.

Tiers (``document_policies.redaction_tier``, default Standard):

* **Minimal**: credentials and secrets, payment cards (Luhn checked).
* **Standard**: Minimal plus government identifiers, bank details (IBAN, labelled account and
  routing numbers) and labelled health data. Business contact names, titles and work emails stay.
* **Strict**: Standard plus every personal identifier: emails, phone numbers, IP addresses and
  person names (labelled name fields, CRM name columns, honorific names, transcript speakers).
  Roles and organisation names stay. There is no named-entity model: unlabelled names in prose
  are not found (stated in docs/open-items.md).

The same original value gets the same placeholder throughout one document (``[EMAIL_1]``), so
the text stays readable and the Internal Only view can reverse it from the stored map. The
original document is kept unchanged in object storage.

GUARANTEE(test_redaction_tiers_redact_what_they_promise)

GUARANTEE(test_a_seeded_pii_cell_never_reaches_the_store_index_or_tools)  # brief acceptance 13
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import partial

from research_files.checks import _PII, _SECRETS, _luhn

Tier = str  # strict | standard | minimal

_GOV = {
    "us_ssn": _PII["us_ssn"],
    "national_id": re.compile(
        r"(?i)\b(?:passport|national id|nin|tax id|tin|ssn|aadhaar|pan)\s*(?:no\.?|number|#)?"
        r"\s*[:#]?\s*([A-Z0-9][A-Z0-9 -]{5,18}[A-Z0-9])"
    ),
}
_BANK = {
    "iban": _PII["iban"],
    "bank_account": re.compile(
        r"(?i)\b(?:account|acct|routing|sort code|swift|bic|aba)\s*(?:no\.?|number|#|code)?"
        r"\s*[:#]\s*([A-Z0-9][A-Z0-9 -]{5,30}[A-Z0-9])"
    ),
}
_HEALTH = {
    "health": re.compile(
        r"(?i)\b(?:diagnos[ie]s|medical (?:record|condition|history)|mrn|health condition|"
        r"prescription|disability)\s*[:=]\s*([^\n;]{2,120})"
    ),
}
_PERSONAL = {
    "email": _PII["email"],
    "phone": _PII["phone"],
    "ip_address": _PII["ip_address"],
    "person_name": re.compile(
        r"(?:\b(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s+[A-Z][a-z'-]+(?:\s+[A-Z][a-z'-]+)?)"
        r"|(?i:\b(?:name|contact|owner|attendees?|participants?|prepared by|author|cc|from|to)"
        r"\s*:\s*)([A-Z][a-z'-]+(?:\s+[A-Z][a-z'-]+){1,2}(?:\s*,\s*[A-Z][a-z'-]+(?:\s+[A-Z][a-z'-]+){1,2})*)"
    ),
}
NAME_COLUMNS = re.compile(
    r"(?i)^(?:.*\b(?:name|contact|owner|person|attendee|rep|employee)\b.*|first|last)$"
)
EMAIL_COLUMNS = re.compile(r"(?i)\b(?:e-?mail|phone|mobile|tel)\b")


def _patterns(tier: Tier) -> list[tuple[str, re.Pattern[str]]]:
    out: list[tuple[str, re.Pattern[str]]] = [*_SECRETS.items(), ("card", _PII["card"])]
    if tier in ("standard", "strict"):
        out += [*_GOV.items(), *_BANK.items(), *_HEALTH.items()]
    if tier == "strict":
        out += list(_PERSONAL.items())
    return out


@dataclass
class Redactor:
    """One document's redactor: consistent placeholders and the map (see the module docstring)."""

    tier: Tier
    by_value: dict[tuple[str, str], str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def placeholder(self, kind: str, original: str) -> str:
        """The placeholder of ``original`` (the same value gets the same placeholder)."""
        key = (kind, original.strip())
        if key not in self.by_value:
            self.counts[kind] = self.counts.get(kind, 0) + 1
            self.by_value[key] = f"[{kind.upper()}_{self.counts[kind]}]"
        return self.by_value[key]

    def text(self, value: str) -> str:
        """``value`` with every value of the tier replaced by its placeholder."""
        out = value
        for kind, pattern in _patterns(self.tier):
            out = pattern.sub(partial(self._sub, kind), out)
        return out

    def _sub(self, kind: str, match: re.Match[str]) -> str:
        whole = match.group(0)
        if kind == "card":
            digits = re.sub(r"\D", "", whole)
            if not (13 <= len(digits) <= 19 and _luhn(digits)):
                return whole
            core = whole.rstrip(" -")
            return self.placeholder(kind, core) + whole[len(core) :]
        # a labelled pattern keeps its label and replaces only the value it captured
        groups = [g for g in match.groups() if g] if match.re.groups else []
        if groups:
            value = groups[-1]
            start = match.start(match.lastindex or 0) - match.start(0)
            if value and start >= 0:
                return whole[:start] + self.placeholder(kind, value) + whole[start + len(value) :]
        return self.placeholder(kind, whole)

    def cell(self, column: str, value: str) -> str:
        """One CRM cell: a name column under Strict is a name whatever its shape."""
        if self.tier == "strict" and value.strip():
            if NAME_COLUMNS.match(column.strip()):
                return self.placeholder("person_name", value)
            if EMAIL_COLUMNS.search(column):
                return self.placeholder("contact_detail", value)
        return self.text(value)

    def speaker(self, label: str) -> str:
        """A transcript speaker label (a person under Strict)."""
        if self.tier == "strict" and label.strip():
            return self.placeholder("person_name", label)
        return label

    def entries(self) -> list[tuple[str, str, str]]:
        """``(placeholder, kind, original)`` for every replacement made."""
        return [(ph, kind, orig) for (kind, orig), ph in self.by_value.items()]

    def present(self, text: str) -> Iterable[tuple[str, str, str]]:
        """The map entries whose placeholder appears in ``text``."""
        return [e for e in self.entries() if e[0] in text]
