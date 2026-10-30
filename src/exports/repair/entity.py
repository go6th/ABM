"""Entity resolution for a job posting (spec rule 4): which business a row belongs to and whether
it counts in the account's numbers. Account-neutral: every name, domain and entity rule comes from
the ``WorkspaceProfile``; the only constants here are generic job-board / applicant-tracking hosts.

``tag_entity`` decides in this order (the first rule that fires wins):

1. **Profile entity rules** (``profile.entities``), in profile order. A rule fires when one of its
   regex patterns matches the title or text (case-insensitive). A rule with a ``before`` date fires
   only for rows first seen before that date (an unknown first-seen date never fires a dated rule).
   A rule with ``before`` and no patterns claims every row first seen before the date (the
   pre-close business). The result is the rule's label and its ``counts_as_account`` flag.
2. **Generic wording of another employer**: "applying for a position with <X>" or "apply to <X>",
   where <X> is a capitalised name that is neither the account (label, name, short name, alias)
   nor the label of a counted entity. The label becomes <X>, which does not count.
3. **The URL host**: a registrable domain in ``profile.domains`` (or ``profile.domain`` /
   ``profile.careers_hosts``) gives the account label, which counts. A host that is neither the
   account's nor a known job-board / applicant-tracking host gives ``NOT_ACCOUNT``, which does
   not count (a look-alike domain is such a host; see ``is_domain_collision``).
4. **Unknown or blank host**, or a job-board host that says nothing about the employer: the
   account label (the row reached this workspace through the account's own scope), which counts.

``is_domain_collision`` flags a host that looks like an account domain but is not one (same name
under another suffix, or the account name inside a hyphenated name).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date

from exports.repair.core import NOT_ACCOUNT, WorkspaceProfile
from exports.repair.keys import fold, host_of, registrable

# Job boards and applicant-tracking hosts: they host many employers, so the host alone says nothing
# about the employer. Generic infrastructure, never an account.
JOB_BOARD_HOSTS = frozenset(
    {
        "indeed.com", "linkedin.com", "glassdoor.com", "ziprecruiter.com", "monster.com",
        "simplyhired.com", "careerbuilder.com", "jooble.org", "adzuna.com", "talent.com",
        "lensa.com", "learn4good.com", "jobrapido.com", "jobisjob.com", "snagajob.com",
        "dice.com", "builtin.com", "wellfound.com", "angel.co", "naukri.com", "reed.co.uk",
        "totaljobs.com", "workopolis.com", "jobbank.gc.ca", "usajobs.gov", "idealist.org",
        "myworkdayjobs.com", "myworkdaysite.com", "greenhouse.io", "lever.co", "icims.com",
        "taleo.net", "smartrecruiters.com", "ashbyhq.com", "jobvite.com", "workable.com",
        "bamboohr.com", "recruitee.com", "breezy.hr", "successfactors.com", "successfactors.eu",
        "oraclecloud.com", "ultipro.com", "paylocity.com", "adp.com", "brassring.com",
        "peopleadmin.com", "governmentjobs.com", "applytojob.com", "jobs2web.com",
    }
)  # fmt: skip

_STOP_FIRST = frozenset(
    {
        "the", "this", "that", "our", "a", "an", "us", "now", "today", "join", "job", "jobs",
        "online", "position", "positions", "role", "roles", "your", "any", "all", "if", "for",
        "with", "please", "click", "both", "each", "apply", "via", "through", "indeed",
        "linkedin", "glassdoor", "ziprecruiter", "monster", "email", "e-mail", "career",
        "careers", "website", "site", "page", "link", "below", "here", "directly", "we", "it",
    }
)  # fmt: skip

_NAME = (
    r"([A-Z][\w&'-]*(?:\.[\w&'-]+)*"
    r"(?:\s+(?:(?:of|and|&)\s+)?[A-Z][\w&'-]*(?:\.[\w&'-]+)*){0,3})"
)
_OTHER_EMPLOYER = re.compile(
    r"(?:(?i:applying\s+for\s+(?:an?\s+|the\s+|this\s+)?(?:[a-z]+\s+){0,2}?"
    r"(?:position|role|job|opportunity)\s+(?:with|at))"
    r"|(?i:\bapply\s+(?:now\s+)?to))\s+" + _NAME
)
_WORDS = re.compile(r"[a-z0-9]+")


def _words(text: str) -> str:
    return " ".join(_WORDS.findall(fold(text)))


def _account_names(profile: WorkspaceProfile) -> list[str]:
    names = [profile.label, profile.account_name, profile.account_short, *profile.aliases]
    return [w for n in names if (w := _words(n))]


def _same_business(candidate: str, names: Iterable[str]) -> bool:
    """Whether ``candidate`` is one of ``names`` or a whole-word part of / around one."""
    cand = _words(candidate)
    if not cand:
        return True
    padded = f" {cand} "
    return any(padded in f" {n} " or f" {n} " in padded for n in names)


def _search(pattern: str, text: str) -> bool:
    try:
        return re.search(pattern, text, re.IGNORECASE) is not None
    except re.error:
        return re.search(re.escape(pattern), text, re.IGNORECASE) is not None


def _day(value: str | date | None) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return (value or "").strip()[:10]


def account_registrables(profile: WorkspaceProfile) -> frozenset[str]:
    """The registrable domains that belong to the account (domains, primary domain, careers)."""
    raw = [*profile.domains, *profile.careers_hosts, profile.domain]
    return frozenset(r for d in raw if d and (r := registrable(host_of(d) or d.lower().strip())))


def is_account_host(profile: WorkspaceProfile, host: str) -> bool:
    """Whether ``host`` belongs to one of the account's registrable domains."""
    host = (host_of(host) or host).lower().strip(".")
    return bool(host) and registrable(host) in account_registrables(profile)


def is_job_board(host: str) -> bool:
    """Whether ``host`` is a known job-board or applicant-tracking host."""
    host = (host_of(host) or host).lower().strip(".")
    return bool(host) and registrable(host) in JOB_BOARD_HOSTS


def _sld(reg: str) -> str:
    return reg.split(".")[0] if reg else ""


def is_domain_collision(profile: WorkspaceProfile, host: str) -> bool:
    """A look-alike domain: ``host`` is not the account's but its name is the account's name under
    another suffix (``acme.org`` for ``acme.com``), the same name without hyphens, or contains the
    account name as a hyphen separated part (``acme-careers.com``). Job boards never collide."""
    host = (host_of(host) or host).lower().strip(".")
    if not host:
        return False
    reg = registrable(host)
    if reg in account_registrables(profile) or reg in JOB_BOARD_HOSTS:
        return False
    names = {_sld(r) for r in account_registrables(profile)}
    names |= {a.replace(" ", "") for a in (_words(x) for x in profile.aliases) if len(a) >= 3}
    names = {n for n in names if len(n) >= 3}
    cand = _sld(reg)
    flat = cand.replace("-", "")
    parts = {p for p in cand.split("-") if p}
    return any(cand == n or flat == n or n in parts for n in names)


def _rule_fires(rule_patterns: tuple[str, ...], before: str | None, first: str, text: str) -> bool:
    if before and not (first and first < before[:10]):
        return False
    if not rule_patterns:
        return bool(before)
    return any(_search(p, text) for p in rule_patterns)


def tag_entity(
    profile: WorkspaceProfile,
    *,
    url: str,
    title: str,
    text: str,
    first_seen: str | date | None,
) -> tuple[str, bool]:
    """The ``(entity label, counts_as_account)`` of a posting; the order of the rules is in the
    module docstring."""
    body = f"{title or ''} {text or ''}"
    first = _day(first_seen)
    for rule in profile.entities:
        if _rule_fires(rule.patterns, rule.before, first, body):
            return rule.label, rule.counts_as_account
    names = _account_names(profile)
    counted = [_words(r.label) for r in profile.entities if r.counts_as_account]
    for m in _OTHER_EMPLOYER.finditer(body):
        other = m.group(1).strip(" .,;:-")
        head = other.split()[0].lower() if other.split() else ""
        if not other or head in _STOP_FIRST or len(other) < 2:
            continue
        if _same_business(other, [*names, *counted]):
            continue
        return other, False
    host = host_of(url)
    if host:
        if is_account_host(profile, host):
            return profile.label, True
        if not is_job_board(host):
            return NOT_ACCOUNT, False
    return profile.label, True
