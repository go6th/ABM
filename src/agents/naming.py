"""G9: tool, provider and person names never reach a client file or an agent prompt.

``client_hits`` combines three checks on text bound for a client:

1. the platform denylist (``safety.client_output.scan_text``: broker names, credit counts);
2. provider display names and provider codes from ``config/routing.yaml`` and
   ``config/providers.yaml`` (internal identities);
3. word digests from ``config/skill/name_digests.yaml`` (the person's name is held only as a
   digest, so it appears nowhere in the repository).

Hits carry the pattern kind and offset, never the matched text.

GUARANTEE(test_g9_planted_tool_names_are_caught_in_every_deliverable)
GUARANTEE(test_g9_person_name_digest_is_caught)
GUARANTEE(test_conversion_operator_only_file_is_internal_only)
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from functools import lru_cache
from pathlib import Path

import yaml

from account_intelligence.config import CONFIG_DIR
from account_intelligence.safety.client_output import scan_text
from agents.errors import GuardViolation

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def word_digest(word: str) -> str:
    """sha256 of the case-folded word."""
    return hashlib.sha256(word.casefold().encode()).hexdigest()


@lru_cache(maxsize=1)
def name_digests(config_dir: Path | None = None) -> frozenset[str]:
    """The forbidden word digests."""
    doc = yaml.safe_load(((config_dir or CONFIG_DIR) / "skill" / "name_digests.yaml").read_text())
    return frozenset(str(d["sha256"]) for d in doc["digests"]["value"])


@lru_cache(maxsize=1)
def provider_terms(config_dir: Path | None = None) -> tuple[str, ...]:
    """Provider display names, broker keys and provider codes (internal identities)."""
    base = config_dir or CONFIG_DIR
    routing = yaml.safe_load((base / "routing.yaml").read_text())
    providers = yaml.safe_load((base / "providers.yaml").read_text())
    terms = {str(b["display_name"]) for b in routing["brokers"].values()}
    terms |= {str(k) for k in routing["brokers"]}
    terms |= {str(k) for k in (providers.get("providers") or {})}
    terms.discard("builtin")
    terms.discard("Built-in web tools")
    return tuple(sorted(terms))


def digest_hits(text: str) -> tuple[int, ...]:
    """Offsets of words whose digest is forbidden."""
    forbidden = name_digests()
    return tuple(m.start() for m in _WORD.finditer(text) if word_digest(m.group()) in forbidden)


def client_hits(text: str, extra_terms: Iterable[str] = ()) -> tuple[tuple[str, int], ...]:
    """Every hit as ``(kind, offset)``: ``denylist``, ``provider`` or ``name``."""
    hits = [("denylist", h.start) for h in scan_text(text)]
    for term in (*provider_terms(), *extra_terms):
        if term.strip():
            pattern = re.compile(r"(?<!\w)" + re.escape(term.strip()) + r"(?!\w)", re.I)
            hits += [("provider", m.start()) for m in pattern.finditer(text)]
    hits += [("name", offset) for offset in digest_hits(text)]
    return tuple(sorted(set(hits)))


@lru_cache(maxsize=1)
def _host_patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    from documents.gate import provider_hosts

    return tuple(
        (host, re.compile(r"(?<![\w-])(?:[\w-]+\.)*" + re.escape(host) + r"(?![\w-])", re.I))
        for host in provider_hosts()
    )


def third_party_domain_hits(text: str) -> tuple[str, ...]:
    """Provider domains named in text, for the output-side and pre-delivery guards."""
    return tuple(host for host, pattern in _host_patterns() if pattern.search(text))


def tool_name_hits(text: str) -> tuple[tuple[str, int], ...]:
    """Remaining configured data-tool names after the output scrub, as (name, offset)."""
    from agents.skill_context import tool_names

    return tuple(
        (name, match.start())
        for name in tool_names()
        for match in re.finditer(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I)
    )


def sanitize_client_value(value: object) -> object:
    """Scrub tool names in nested output, then fail closed on remaining client terms/domains."""
    from agents.skill_context import strip_tool_names

    if isinstance(value, str):
        if third_party_domain_hits(value):
            raise GuardViolation("G9", "third_party_domain")
        cleaned = strip_tool_names(value)
        if tool_name_hits(cleaned) or client_hits(cleaned):
            raise GuardViolation("G9", "client_output_leak")
        return cleaned
    if isinstance(value, Mapping):
        return {key: sanitize_client_value(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize_client_value(nested) for nested in value]
    return value


def assert_client_clean(texts: Iterable[str], extra_terms: Iterable[str] = ()) -> None:
    """Raise ``GuardViolation(G9)`` with a reason if client text contains a guarded term."""
    extra = tuple(extra_terms)
    values = tuple(texts)
    domains = sum(len(third_party_domain_hits(text)) for text in values)
    if domains:
        raise GuardViolation("G9", "third_party_domain", f"{domains} hit(s)")
    count = sum(len(tool_name_hits(text)) + len(client_hits(text, extra)) for text in values)
    if count:
        raise GuardViolation("G9", "client_output_leak", f"{count} hit(s)")
