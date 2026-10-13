"""Which company uploaded material is about, from the material itself; never a guess.

Evidence, scored per candidate: email domains (not free mail and not the workspace's own
domains), web addresses, values of company or account columns in CRM exports, and names with a
legal suffix (Inc, Ltd, GmbH...). Names and domains that belong together merge (``Acme Corp``
and ``acme.com``). The result is ``resolved`` only when the best candidate has a domain, reaches
``min_score`` and beats the second by ``ambiguity_margin`` of its score; otherwise it is
ambiguous and the user must confirm (the run refuses to start until then).

GUARANTEE(test_ambiguous_company_blocks_the_run_until_confirmed)
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from documents.config import CompanyCfg
from documents.sandbox import ParsedUnit

_EMAIL_DOMAIN = re.compile(r"\b[A-Za-z0-9._%+-]{1,64}@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b")
_URL_HOST = re.compile(
    r"(?<![@\w.-])(?:https?://)?(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|io|co|net|org|ai|de|uk|fr|in|us|eu|biz|tech))\b",
    re.I,
)
_SUFFIX = re.compile(
    r"\b([A-Z][A-Za-z0-9&'-]*(?:\s+[A-Z][A-Za-z0-9&'-]*){0,3})\s*,?\s+"
    r"(Inc\.?|LLC|Ltd\.?|Limited|GmbH|plc|PLC|Corp\.?|Corporation|AG|S\.A\.|SA|B\.V\.|BV|N\.V\.|Pty|SE)\b"
)
_COMPANY_COLUMN = re.compile(
    r"(?i)\b(company|account|organi[sz]ation|customer|client|prospect|org)\b(?!.*\b(id|owner|manager)\b)"
)
_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov"}


@dataclass
class Candidate:
    """One company the material may be about."""

    name: str | None = None
    domain: str | None = None
    score: float = 0.0
    signals: dict[str, int] = field(default_factory=dict)

    def add(self, signal: str, weight: float) -> None:
        """Count one piece of evidence."""
        self.signals[signal] = self.signals.get(signal, 0) + 1
        # each kind of signal saturates: ten emails are not ten times the evidence of one
        if self.signals[signal] <= 10:
            self.score += weight

    def view(self) -> dict[str, Any]:
        """JSON form (stored on the document and shown to the user)."""
        return {
            "name": self.name,
            "domain": self.domain,
            "score": round(self.score, 2),
            "signals": dict(sorted(self.signals.items())),
        }


def registrable(host: str) -> str:
    """``mail.eu.acme.co.uk`` to ``acme.co.uk``; ``www.acme.com`` to ``acme.com``."""
    parts = host.lower().strip(".").split(".")
    if len(parts) >= 3 and parts[-2] in _SECOND_LEVEL and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _label(domain: str) -> str:
    return registrable(domain).split(".")[0]


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


class Resolver:
    """Collects evidence unit by unit (raw text, before redaction), then decides."""

    def __init__(self, cfg: CompanyCfg, exclude_domains: Iterable[str]):
        self.cfg = cfg
        self.free = {d.lower() for d in cfg.free_mail_domains}
        self.exclude = {registrable(d) for d in exclude_domains if d}
        self.by_domain: dict[str, Candidate] = {}
        self.by_name: dict[str, Candidate] = {}

    def _domain(self, host: str, signal: str, weight: float) -> None:
        domain = registrable(host)
        if domain in self.free or domain in self.exclude or "." not in domain:
            return
        cand = self.by_domain.setdefault(domain, Candidate(domain=domain))
        cand.add(signal, weight)

    def _name(self, name: str, signal: str, weight: float) -> None:
        clean = " ".join(name.split())[:120]
        key = _key(clean)
        if len(key) < 2:
            return
        cand = self.by_name.setdefault(key, Candidate(name=clean))
        cand.add(signal, weight)

    def feed(self, unit: ParsedUnit) -> None:
        """Evidence from one parsed unit (its heading included: titles often name the company)."""
        text = f"{unit.heading}\n{unit.text}" if unit.heading else unit.text
        if unit.rows is not None:
            header = unit.header or []
            for _, cells in unit.rows:
                for i, cell in enumerate(cells):
                    column = header[i] if i < len(header) else ""
                    value = "" if cell is None else str(cell)
                    if column and _COMPANY_COLUMN.search(column) and value.strip():
                        self._name(value, "company_column", 2.0)
                    for m in _EMAIL_DOMAIN.finditer(value):
                        self._domain(m.group(1), "email_domain", 1.0)
            return
        for m in _EMAIL_DOMAIN.finditer(text):
            self._domain(m.group(1), "email_domain", 1.0)
        for m in _URL_HOST.finditer(text):
            self._domain(m.group(1), "web_address", 0.5)
        for m in _SUFFIX.finditer(text):
            self._name(f"{m.group(1)} {m.group(2)}", "legal_name", 1.5)

    def candidates(self) -> list[Candidate]:
        """Merged candidates, best first."""
        merged: dict[str, Candidate] = {}
        for domain, cand in self.by_domain.items():
            merged[_label(domain)] = Candidate(None, domain, cand.score, dict(cand.signals))
        for key, cand in self.by_name.items():
            target = next(
                (
                    m
                    for label, m in merged.items()
                    if key.startswith(label)
                    or label.startswith(key[: len(label)])
                    and len(label) >= 3
                ),
                None,
            )
            if target is None:
                merged[key] = Candidate(cand.name, None, cand.score, dict(cand.signals))
                continue
            target.name = target.name or cand.name
            target.score += cand.score
            for s, n in cand.signals.items():
                target.signals[s] = target.signals.get(s, 0) + n
        for cand in merged.values():
            if cand.name is None and cand.domain:
                cand.name = _label(cand.domain).replace("-", " ").title()
        return sorted(merged.values(), key=lambda c: (-c.score, c.name or "", c.domain or ""))

    def decide(self) -> dict[str, Any]:
        """``status`` (resolved, ambiguous or none), ``company``, ``candidates`` and ``reason``."""
        cands = self.candidates()
        views = [c.view() for c in cands[:5]]
        if not cands or cands[0].score < self.cfg.min_score:
            return {
                "status": "none",
                "company": None,
                "candidates": views,
                "reason": "too_little_evidence",
            }
        top = cands[0]
        if len(cands) > 1 and cands[1].score >= top.score * (1 - self.cfg.ambiguity_margin):
            return {
                "status": "ambiguous",
                "company": None,
                "candidates": views,
                "reason": "close_candidates",
            }
        if top.domain is None:
            return {
                "status": "ambiguous",
                "company": None,
                "candidates": views,
                "reason": "no_domain",
            }
        return {"status": "resolved", "company": top.view(), "candidates": views, "reason": None}


def combine(results: Iterable[dict[str, Any] | None]) -> dict[str, Any]:
    """One decision over several documents: resolved only when every resolved document names the
    same domain and none is ambiguous."""
    seen = [r for r in results if r]
    domains = {r["company"]["domain"] for r in seen if r.get("status") == "resolved"}
    if any(r.get("status") == "ambiguous" for r in seen) or len(domains) > 1:
        cands: dict[str, dict[str, Any]] = {}
        for r in seen:
            for c in r.get("candidates") or []:
                cands.setdefault(c.get("domain") or c.get("name") or "", c)
        return {
            "status": "ambiguous",
            "company": None,
            "candidates": list(cands.values())[:5],
            "reason": "documents_disagree" if len(domains) > 1 else "close_candidates",
        }
    if len(domains) == 1:
        first = next(r for r in seen if r.get("status") == "resolved")
        return {
            "status": "resolved",
            "company": first["company"],
            "candidates": first["candidates"],
            "reason": None,
        }
    return {"status": "none", "company": None, "candidates": [], "reason": "too_little_evidence"}
