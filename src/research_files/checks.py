"""Deterministic audit checks (pure; no I/O, no model).

A ``Finding`` has a severity (info, warn, error), a stable check id, the exact location (sheet,
row, page, member...), a message and a suggested fix. Checks report; they never change the data.

* Sensitive data: emails, phone numbers, card numbers (Luhn-checked), national id shapes, IBANs,
  IP addresses, and secrets (provider keys, cloud access keys, private keys, passwords in text).
  A finding names the kind and where it is, never the value.
* Text: encoding replacement characters, empty sections, quantitative claims without a nearby
  source, and the same quantity stated with different numbers.

GUARANTEE(test_sensitive_data_is_flagged_without_its_value)
GUARANTEE(test_research_checks_find_unsourced_and_contradicting_numbers)
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

Severity = str  # "info" | "warn" | "error"


@dataclass(frozen=True)
class Finding:
    """One audit result (see the module docstring)."""

    severity: Severity
    check: str
    location: dict[str, Any]
    message: str
    fix: str | None = None


_PII: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}\b"),
    "phone": re.compile(
        r"(?<![\w.])(?:\+\d{1,3}[ .-]?\(?\d{1,4}\)?(?:[ .-]?\d{2,4}){2,3}"
        r"|\(\d{3}\)\s?\d{3}[ .-]\d{4}|\d{3}[.-]\d{3}[.-]\d{4})(?![\w.])"
    ),
    "card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "us_ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b"),
    "ip_address": re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
    ),
}
_SECRETS: dict[str, re.Pattern[str]] = {
    "provider_api_key": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    "cloud_access_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "password_assignment": re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|token)\s*[:=]\s*\S{6,}"
    ),
    "bearer_token": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}"),
}  # fmt: skip
# one combined pre-filter so a row without any candidate costs one regex search
_ANY = re.compile(
    r"@|\d{3}|-----BEGIN|sk-|AKIA|ASIA|(?i:password|passwd|pwd|secret|api[_-]?key|token|bearer)"
)


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        n = ord(ch) - 48
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def sensitive_kinds(text: str) -> list[str]:
    """The kinds of sensitive data present in ``text`` (never the values), in a stable order."""
    if not text or not _ANY.search(text):
        return []
    found: list[str] = []
    for kind, pattern in _SECRETS.items():
        if pattern.search(text):
            found.append(kind)
    for kind, pattern in _PII.items():
        for match in pattern.finditer(text):
            if kind == "card":
                digits = re.sub(r"\D", "", match.group())
                if not (13 <= len(digits) <= 19 and _luhn(digits)):
                    continue
            found.append(kind)
            break
    return found


SECRET_KINDS = frozenset(_SECRETS)


def redact_secrets(text: str) -> str:
    """The text with secrets replaced (sent to a model or shown in an answer)."""
    out = text
    for pattern in _SECRETS.values():
        out = pattern.sub("[secret removed]", out)
    return out


def mask(text: str) -> str:
    """The text with every sensitive value replaced by its kind (for public analytics)."""
    out = redact_secrets(text)
    for kind, pattern in _PII.items():
        out = pattern.sub(f"[{kind}]", out)
    return out


# ------------------------------------------------------------------ text research checks

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
_QUANT = re.compile(r"(?<![\w.])[-+]?\$?\d[\d,]*(?:\.\d+)?\s?(?:%|percent|million|billion|bn|m|k)?")
_SOURCE = re.compile(
    r"\[\d+\]|\(\s*[A-Z][A-Za-z-]+(?: et al\.)?,? \d{4}\s*\)|https?://|\bsource[s]?:|"
    r"\baccording to\b|\bcited\b|\breport(?:ed)? by\b",
    re.I,
)
_CLAIM = re.compile(
    r"\b(increase[ds]?|decrease[ds]?|grew|grow[sn]?|rose|fell|declin\w*|"
    r"show[sn]?|found|account[s]? for|represent[s]?|reach\w*|total\w*|"
    r"revenue|market|share|growth|percent|%)",
    re.I,
)
_METRIC = re.compile(
    r"\b((?:[a-z]+\s){0,2}(?:revenue|sales|profit|income|growth|share|headcount|employees|"
    r"customers|users|margin|cost|costs|price|budget|population))\b\s*(?:of|was|is|were|at|:)?\s*"
    r"(\$?[\d][\d,]*(?:\.\d+)?\s?(?:%|percent|million|billion|bn|m|k)?)",
    re.I,
)


def sentences(text: str) -> Iterator[str]:
    """Sentences of a text (simple splitter, good enough for checks)."""
    for match in _SENTENCE.finditer(text):
        s = match.group().strip()
        if len(s) > 3:
            yield s


def unsourced_claims(text: str) -> list[str]:
    """Quantitative claim sentences with no source marker in them or right after them."""
    out = []
    items = list(sentences(text))
    for i, s in enumerate(items):
        if not (_QUANT.search(s) and _CLAIM.search(s)):
            continue
        window = s + " " + (items[i + 1] if i + 1 < len(items) else "")
        if not _SOURCE.search(window):
            out.append(s[:240])
    return out


def _norm_number(raw: str) -> float | None:
    text = raw.lower().replace("$", "").replace(",", "").strip()
    scale = 1.0
    for suffix, factor in (("billion", 1e9), ("bn", 1e9), ("million", 1e6), ("m", 1e6), ("k", 1e3)):
        if text.endswith(suffix):
            text, scale = text[: -len(suffix)].strip(), factor
            break
    text = text.replace("percent", "").replace("%", "").strip()
    try:
        return float(text) * scale
    except ValueError:
        return None


@dataclass
class MetricLedger:
    """Each stated metric with its first value and location, to find contradictions across a
    document. Bounded (``cap`` metrics)."""

    seen: dict[str, list[Any]] = field(default_factory=dict)  # metric -> [value, raw, location]
    cap: int = 500

    def check(self, text: str, location: dict[str, Any]) -> list[Finding]:
        """Findings for metrics in ``text`` that contradict an earlier statement."""
        out: list[Finding] = []
        for match in _METRIC.finditer(text):
            metric = " ".join(match.group(1).lower().split())
            raw = match.group(2).strip()
            value = _norm_number(raw)
            if value is None:
                continue
            prior = self.seen.get(metric)
            if prior is None:
                if len(self.seen) < self.cap:
                    self.seen[metric] = [value, raw, location]
                continue
            if prior[0] != value and not _close(prior[0], value):
                out.append(
                    Finding(
                        "warn",
                        "contradicting_numbers",
                        location,
                        f'"{metric}" is stated as {raw} here and as {prior[1]} at'
                        f" {describe(prior[2])}.",
                        "Check which figure is right, or say why the two differ (period, scope).",
                    )
                )
        return out


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1.0)


def describe(location: dict[str, Any]) -> str:
    """A short human form of a location."""
    parts = []
    for key in ("member", "sheet", "table", "page", "slide", "section", "rows", "row", "line"):
        if key in location and location[key] not in (None, ""):
            value = location[key]
            if key == "rows" and isinstance(value, list) and len(value) == 2:
                value = f"{value[0]}-{value[1]}"
            parts.append(f"{key} {value}")
    return ", ".join(parts) or "the file"


def text_findings(
    text: str, location: dict[str, Any], *, pii: bool, ledger: MetricLedger | None
) -> list[Finding]:
    """Encoding, sensitive data and research checks on one unit of document text."""
    out: list[Finding] = []
    bad = text.count("�")
    if bad:
        out.append(
            Finding(
                "warn",
                "encoding_problem",
                location,
                f"{bad} character(s) could not be decoded and were replaced.",
                "Save the file as UTF-8 and upload it again.",
            )
        )
    if pii:
        for kind in sensitive_kinds(text):
            out.append(sensitive_finding(kind, location))
    for claim in unsourced_claims(text)[:5]:
        out.append(
            Finding(
                "info",
                "claim_without_source",
                location,
                f'A quantitative claim has no source near it: "{mask(claim)[:200]}"',
                "Add a citation or a link to where the number comes from.",
            )
        )
    if ledger is not None:
        out.extend(ledger.check(text, location))
    return out


def sensitive_finding(kind: str, location: dict[str, Any]) -> Finding:
    """The finding for one kind of sensitive data at a location (the value is never included)."""
    secret = kind in SECRET_KINDS
    return Finding(
        "error" if secret else "warn",
        "secret_found" if secret else "personal_data_found",
        {**location, "kind": kind},
        f"{'A secret' if secret else 'Personal data'} ({kind.replace('_', ' ')}) appears here."
        " Its value is not shown in this report.",
        "Remove or rotate the secret before sharing this file."
        if secret
        else "Remove the personal data, or confirm you are allowed to process it.",
    )


def empty_sections(sections: Iterable[tuple[str, str, dict[str, Any]]]) -> list[Finding]:
    """A heading followed by no text. ``sections`` yields ``(heading, body, location)``."""
    out = []
    for heading, body, location in sections:
        if heading and not body.strip():
            out.append(
                Finding(
                    "warn",
                    "empty_section",
                    location,
                    f'The section "{heading[:120]}" has no content.',
                    "Write the section or remove the heading.",
                )
            )
    return out
