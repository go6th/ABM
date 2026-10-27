"""The entity lock (master prompt v2 section 3): the company is locked before any pull, and
every record of every later step passes ``accept`` or is dropped and logged.

Why: on 2026-10-24 a run pulled jobs and the tech stack from ``vizent.co`` (an Indian 3D studio)
for an account named Vizient. The lock reads the account's own home page first and refuses to
research a domain whose home page does not name the account; it records the hosts that belong
to the account (its domain, the host it redirects to, subsidiaries, the verified careers boards)
and the look-alike brands it must never accept (a brand within edit distance 2 of the account's,
on a host that is not the account's). Pure: no I/O; the graph node feeds it the page and the
search hits and stores the result in the run state.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from datamodel.hosts import brand_label, host_of, registrable

_WORD = re.compile(r"[a-z0-9]+")
# a home page with less text than this (a challenge page, a script shell) proves nothing
MIN_PAGE_CHARS = 80


def name_tokens(name: str, stopwords: Iterable[str]) -> tuple[str, ...]:
    """The lower-case alphanumeric tokens of a company name without the legal-form words."""
    stop = {w.lower() for w in stopwords}
    return tuple(t for t in _WORD.findall(name.lower()) if t not in stop)


def brand_token(name: str, stopwords: Iterable[str]) -> str:
    """The token that identifies the brand: the first token of the name that is not a legal-form
    word ("Vizient, Inc." is ``vizient``; "The Kraft Heinz Company" is ``kraft``). The whole name
    folded when every token is a stopword."""
    tokens = name_tokens(name, stopwords)
    if tokens:
        return tokens[0]
    folded = "".join(_WORD.findall(name.lower()))
    return folded or name.lower()


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (small strings; brands are a word or two)."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@dataclass(frozen=True)
class Lookalike:
    """A brand the lock must never accept: a host whose name is close to the account's."""

    host: str
    title: str
    why: str


@dataclass(frozen=True)
class EntityLock:
    """What belongs to the account and what must be refused (section 3 of the master prompt)."""

    legal_name: str
    brand: str
    primary_domain: str
    allowed_domains: frozenset[str]  # registrable domains of the account and its subsidiaries
    careers_hosts: frozenset[str] = frozenset()
    subsidiaries: tuple[tuple[str, str], ...] = ()  # (name, registrable domain or "")
    blocked_lookalikes: tuple[Lookalike, ...] = ()
    home_url: str = ""
    redirected_to: str = ""
    home_title: str = ""
    confirmed: bool = False  # the home page of the primary domain names the account
    # the domain is a look-alike of the account's brand and its home page does not name the
    # account: the only case that stops a run. A site that merely does not say the legal name
    # (gsk.com for GlaxoSmithKline) is unconfirmed, never a mismatch.
    mismatch: bool = False
    problems: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        """JSON-ready form for the run state and the workspace file ``entity_lock.json``."""
        return {
            "legal_name": self.legal_name,
            "brand": self.brand,
            "primary_domain": self.primary_domain,
            "allowed_domains": sorted(self.allowed_domains),
            "careers_hosts": sorted(self.careers_hosts),
            "subsidiaries": [{"name": n, "domain": d} for n, d in self.subsidiaries],
            "blocked_lookalikes": [
                {"host": x.host, "title": x.title, "why": x.why} for x in self.blocked_lookalikes
            ],
            "home_url": self.home_url,
            "redirected_to": self.redirected_to,
            "home_title": self.home_title,
            "confirmed": self.confirmed,
            "mismatch": self.mismatch,
            "problems": list(self.problems),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> EntityLock:
        """The inverse of ``as_dict`` (a checkpointed state carries the dict form)."""

        def items(key: str) -> list[Any]:
            value = raw.get(key)
            return list(value) if isinstance(value, (list, tuple, set, frozenset)) else []

        subs = tuple(
            (str(s.get("name", "")), str(s.get("domain", "")))
            for s in items("subsidiaries")
            if isinstance(s, dict)
        )
        looks = tuple(
            Lookalike(str(x.get("host", "")), str(x.get("title", "")), str(x.get("why", "")))
            for x in items("blocked_lookalikes")
            if isinstance(x, dict)
        )
        return cls(
            legal_name=str(raw.get("legal_name", "")),
            brand=str(raw.get("brand", "")),
            primary_domain=str(raw.get("primary_domain", "")),
            allowed_domains=frozenset(str(d) for d in items("allowed_domains")),
            careers_hosts=frozenset(str(d) for d in items("careers_hosts")),
            subsidiaries=subs,
            blocked_lookalikes=looks,
            home_url=str(raw.get("home_url", "")),
            redirected_to=str(raw.get("redirected_to", "")),
            home_title=str(raw.get("home_title", "")),
            confirmed=bool(raw.get("confirmed", False)),
            mismatch=bool(raw.get("mismatch", False)),
            problems=tuple(str(p) for p in items("problems")),
        )


def names_brand(text: str | None, brand: str, legal_name: str = "") -> bool:
    """True when ``text`` names the brand as a whole word (case folded), or the legal name."""
    if not text:
        return False
    folded = text.lower()
    if brand and re.search(rf"(?<![a-z0-9]){re.escape(brand)}(?![a-z0-9])", folded):
        return True
    legal = " ".join(_WORD.findall(legal_name.lower()))
    return bool(legal) and legal in " ".join(_WORD.findall(folded))


def home_page_confirms(
    name: str, title: str | None, text: str | None, stopwords: Iterable[str]
) -> bool:
    """The home page belongs to the account when its title or text names the brand token.
    (vizent.co's home page never says "Vizient".)"""
    brand = brand_token(name, stopwords)
    return names_brand(title, brand, name) or names_brand(text, brand, name)


def find_lookalikes(
    brand: str,
    hits: Iterable[tuple[str, str]],
    allowed: Iterable[str],
    *,
    max_distance: int = 2,
) -> tuple[Lookalike, ...]:
    """Hosts among search ``hits`` (host, title) whose brand label is within ``max_distance`` of
    the account's brand but is not the account's: a different company with a near name."""
    allowed_set = {registrable(a) for a in allowed}
    out: list[Lookalike] = []
    seen: set[str] = set()
    for host, title in hits:
        reg = registrable(host_of(host) or host)
        if not reg or reg in allowed_set or reg in seen:
            continue
        label = reg.split(".", 1)[0]
        if label == brand:
            continue  # the brand itself on another registry (a country site) is not a look-alike
        distance = edit_distance(label, brand)
        if 0 < distance <= max_distance and len(label) >= 4:
            seen.add(reg)
            out.append(Lookalike(reg, title[:120], f"brand within edit distance {distance}"))
    return tuple(out)


def build_lock(
    *,
    legal_name: str,
    primary_domain: str,
    stopwords: Iterable[str],
    home_url: str,
    final_url: str | None,
    home_title: str | None,
    home_text: str | None,
    subsidiaries: Sequence[tuple[str, str]] = (),
    careers_hosts: Iterable[str] = (),
    search_hits: Iterable[tuple[str, str]] = (),
    max_distance: int = 2,
) -> EntityLock:
    """Assemble the lock from what the node read (the home page, the company record, the
    careers host, one brand search). ``confirmed`` is False when the home page does not name the
    account: the run must not go on researching that domain."""
    brand = brand_token(legal_name, stopwords)
    allowed = {registrable(primary_domain)}
    redirected = host_of(final_url) if final_url else ""
    if redirected:
        allowed.add(registrable(redirected))
    subs = tuple((n, registrable(d) if d else "") for n, d in subsidiaries if n)
    allowed.update(d for _n, d in subs if d)
    careers = frozenset(host_of(h) or h for h in careers_hosts if h)
    label = brand_label(primary_domain)
    # a domain whose name carries the brand (vizientinc.com for Vizient) is the account's
    # whatever its home page says; a blocked or script-only page cannot unlock or stop a run
    related = bool(brand) and len(label) >= 4 and (brand in label or label in brand)
    confirmed = related or home_page_confirms(legal_name, home_title, home_text, stopwords)
    readable = len((home_text or "").strip()) >= MIN_PAGE_CHARS
    distance = edit_distance(label, brand) if label and brand else 99
    mismatch = (not confirmed) and readable and 0 < distance <= max_distance and len(label) >= 4
    problems: list[str] = []
    if not confirmed:
        problems.append(f"home page of {primary_domain} does not name {legal_name}")
    lookalikes = find_lookalikes(brand, search_hits, allowed, max_distance=max_distance)
    return EntityLock(
        legal_name=legal_name,
        brand=brand,
        primary_domain=registrable(primary_domain),
        allowed_domains=frozenset(allowed),
        careers_hosts=careers,
        subsidiaries=subs,
        blocked_lookalikes=lookalikes,
        home_url=home_url,
        redirected_to=redirected,
        home_title=(home_title or "")[:200],
        confirmed=confirmed,
        mismatch=mismatch,
        problems=tuple(problems),
    )


def accept(lock: EntityLock, url: str | None, text: str | None = None) -> tuple[bool, str]:
    """The section 3 ``accept`` function. A record is kept when its host belongs to the account
    (allowed domain, subsidiary or careers board), or when it is a third-party page whose text
    names the brand and names no blocked look-alike. Otherwise (False, reason) and the caller
    logs ``DROPPED: entity mismatch (<reason>)``."""
    host = host_of(url)
    if host:
        reg = registrable(host)
        if reg in lock.allowed_domains or host in lock.careers_hosts:
            return True, "own_host"
        for bad in lock.blocked_lookalikes:
            if reg == bad.host:
                return False, f"lookalike host {bad.host}"
    if not text:
        return False, "no account name in text" if host else "no url"
    for bad in lock.blocked_lookalikes:
        label = bad.host.split(".", 1)[0]
        names_bad = label and names_brand(text, label)
        if names_bad and not names_brand(text, lock.brand, lock.legal_name):
            return False, f"names lookalike {bad.host}"
    if names_brand(text, lock.brand, lock.legal_name):
        return True, "third_party_names_account"
    return False, "no account name in text"
