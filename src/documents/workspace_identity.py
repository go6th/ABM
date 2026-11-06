"""Which company an uploaded workspace zip is about, and whether that is the account it was
uploaded to (2026-11-05, after the UCB "everything" bundle).

A workspace package names its own company in its raw pulls and its restore note: the company
record (``data/company_record.json``: ``domain``, ``company_name``, ``pulled``), the organisation
resolve pull (``data/org_resolve.json``: ``name``, ``params.url``), the restore note's heading
(``# UCB workspace (ucb.com), October 24, 2026``) and the careers host of its live posting links.
``identity_of`` reads them, pure, from the member bytes by path.

``decide`` compares that identity with the account the file was uploaded to. The account owns
its domain and every domain bound to it (``watch_config.domains``, the entity lock's allowed
domains). When the workspace's registrable domain is one of them the import proceeds; when it is
another domain the import is HELD and the user is asked whether the two are the same company
(``documents.service.same_company``) or whether the file belongs to another account
(``confirm-company``). Nothing is written to the account's tables until the user answers: the
UCB bundle of 2026-11-04 was built for an account whose domain was not ucb.com, so every posting
of careers.ucb.com was tagged "Not the account" and the technology, signal and coverage tables
emptied out behind it.

The research date is the day the workspace was pulled (the company record's ``pulled``, else the
restore note's date): the imported rows are dated by it, and the "everything" bundle's window
ends on it, not on the day of the upload.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from documents.company import registrable
from documents.workspace import MemberRole

_RESTORE_HEAD = re.compile(
    r"^#\s*(?P<name>.+?)\s+workspace\s*\((?P<domain>[^)\s]+)\)\s*(?:,\s*(?P<date>.+?))?\s*$"
)
_MONTH_DATE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),?\s+(\d{4})\b"
)
_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_MONTHS = {
    m: i
    for i, m in enumerate(
        (
            "January", "February", "March", "April", "May", "June", "July", "August",
            "September", "October", "November", "December",
        ),
        start=1,
    )
}  # fmt: skip
_DOMAIN = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")
MAX_MEMBER_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class WorkspaceIdentity:
    """What the package says about its own company."""

    domain: str = ""  # registrable, lower case ("ucb.com"); "" when the package names none
    name: str = ""  # "UCB"
    research_date: date | None = None
    careers_hosts: tuple[str, ...] = ()  # "careers.ucb.com"
    evidence: tuple[str, ...] = ()  # which members said so, for the log and the prompt
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def found(self) -> bool:
        """Whether the package names a company domain."""
        return bool(self.domain)

    def as_dict(self) -> dict[str, Any]:
        """JSON for ``documents.detected_company['workspace']``."""
        return {
            "domain": self.domain,
            "name": self.name,
            "research_date": self.research_date.isoformat() if self.research_date else None,
            "careers_hosts": list(self.careers_hosts),
            "evidence": list(self.evidence),
            "aliases": list(self.aliases),
        }


def _clean_domain(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if "://" in text or text.startswith("www."):
        host = urlsplit(text if "://" in text else "https://" + text).hostname or ""
        text = host
    text = text.strip(".").split("/")[0]
    if not _DOMAIN.match(text):
        return ""
    return registrable(text)


def parse_day(value: Any) -> date | None:
    """A day from ``2026-10-24``, a timestamp, or ``October 24, 2026``; None otherwise."""
    text = str(value or "").strip()
    m = _ISO.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = _MONTH_DATE.search(text)
    if m:
        try:
            return date(int(m.group(3)), _MONTHS[m.group(1)], int(m.group(2)))
        except ValueError:
            return None
    return None


def _loads(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, TypeError):
        return None


def _base(member: str) -> str:
    return member.rsplit("/", 1)[-1].casefold()


def _careers_hosts(data: Any, own: str) -> list[str]:
    """Careers hosts of the live posting links, restricted to the workspace's own domain."""
    hosts: dict[str, int] = {}
    items = data if isinstance(data, list) else []
    for item in items[:5000]:
        url = str((item or {}).get("url") or "") if isinstance(item, Mapping) else ""
        host = (urlsplit(url).hostname or "").lower() if url.startswith("http") else ""
        if host and (not own or registrable(host) == own):
            hosts[host] = hosts.get(host, 0) + 1
    return sorted(hosts, key=lambda h: (-hosts[h], h))


def identity_of(members: Mapping[str, bytes], roles: Mapping[str, MemberRole]) -> WorkspaceIdentity:
    """The company the package names (see the module docstring). Pure: members by path. A
    member past ``MAX_MEMBER_BYTES`` is not read (the identity never needs a big pull)."""
    domain = name = ""
    research: date | None = None
    evidence: list[str] = []
    aliases: list[str] = []
    live_index: Any = None
    restore_date: date | None = None
    for member in sorted(members):
        role = roles.get(member)
        raw = members[member]
        if len(raw) > MAX_MEMBER_BYTES:
            continue
        base = _base(member)
        if (
            role is MemberRole.RAW_PULL
            and base.startswith("company_record")
            and base.endswith(".json")
        ):
            data = _loads(raw)
            if isinstance(data, Mapping):
                found = _clean_domain(data.get("domain"))
                if found and (not domain or found == domain):
                    domain, evidence = found, [*evidence, member]
                label = str(data.get("company_name") or data.get("name") or "").strip()
                if label and not name:
                    name = label
                elif label:
                    aliases.append(label)
                research = research or parse_day(data.get("pulled"))
        elif (
            role is MemberRole.RAW_PULL
            and base.startswith("org_resolve")
            and base.endswith(".json")
        ):
            data = _loads(raw)
            if isinstance(data, Mapping):
                raw_params = data.get("params")
                params: Mapping[str, Any] = raw_params if isinstance(raw_params, Mapping) else {}
                found = _clean_domain(params.get("url"))
                if found and (not domain or found == domain):
                    domain, evidence = found, [*evidence, member]
                label = str(data.get("name") or params.get("name") or "").strip()
                if label and not name:
                    name = label
                elif label and label not in aliases:
                    aliases.append(label)
        elif role is MemberRole.RAW_PULL and base.startswith("live_postings_index"):
            live_index = _loads(raw)
        elif role is MemberRole.RESTORE:
            head = raw.decode("utf-8", errors="replace").lstrip().split("\n", 1)[0].strip()
            m = _RESTORE_HEAD.match(head)
            if m:
                found = _clean_domain(m.group("domain"))
                if found and (not domain or found == domain):
                    domain, evidence = found, [*evidence, member]
                label = m.group("name").strip()
                if label and not name:
                    name = label
                elif label and label not in aliases:
                    aliases.append(label)
                restore_date = parse_day(m.group("date"))
            elif not restore_date:
                restore_date = parse_day(head)
    hosts = _careers_hosts(live_index, domain) if live_index is not None else []
    if not name and domain:
        name = (
            domain.split(".")[0].upper()
            if len(domain.split(".")[0]) <= 4
            else domain.split(".")[0].title()
        )
    return WorkspaceIdentity(
        domain=domain,
        name=name,
        research_date=research or restore_date,
        careers_hosts=tuple(hosts),
        evidence=tuple(evidence),
        aliases=tuple(a for a in dict.fromkeys(aliases) if a != name),
    )


IDENTITY_PULLS = ("company_record", "org_resolve", "live_postings_index")


def read_identity_members(stream: Any, roles: Mapping[str, MemberRole]) -> dict[str, bytes]:
    """The few small members ``identity_of`` reads (the company record, the organisation
    resolve pull, the live posting index and the restore note), by path, from the zip bytes or
    stream. Nothing else is read: a workspace zip can be large."""
    import io
    import zipfile

    data = stream.read() if hasattr(stream, "read") else stream
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            role = roles.get(info.filename)
            if info.is_dir() or info.file_size > MAX_MEMBER_BYTES:
                continue
            base = _base(info.filename)
            wanted = role is MemberRole.RESTORE or (
                role is MemberRole.RAW_PULL
                and base.endswith(".json")
                and base.startswith(IDENTITY_PULLS)
            )
            if wanted:
                out[info.filename] = archive.read(info)
    return out


def account_bound_domains(domain: str, watch_config: Mapping[str, Any] | None) -> frozenset[str]:
    """Every registrable domain the account owns: its own and the ones bound to it
    (``watch_config.domains``)."""
    out = {_clean_domain(domain)} if domain else set()
    for d in (watch_config or {}).get("domains") or ():
        found = _clean_domain(d)
        if found:
            out.add(found)
    return frozenset(x for x in out if x)


def decide(identity: WorkspaceIdentity, bound: frozenset[str]) -> str:
    """``same`` when the package names one of the account's domains (or none), ``mismatch``
    when it names another one."""
    if not identity.found:
        return "same"
    return "same" if identity.domain in bound else "mismatch"


def bound_watch_config(
    watch_config: Mapping[str, Any] | None, identity: WorkspaceIdentity
) -> dict[str, Any]:
    """The account's watch config with the workspace's domain, name and careers hosts bound to
    it (the user said the two are the same company). Existing entries are kept."""
    out: dict[str, Any] = dict(watch_config or {})
    domains = [str(d) for d in out.get("domains") or () if str(d).strip()]
    if identity.domain and identity.domain not in {_clean_domain(d) for d in domains}:
        domains.append(identity.domain)
    out["domains"] = domains
    aliases = [str(a) for a in out.get("aliases") or () if str(a).strip()]
    for label in (identity.name, *identity.aliases):
        if label and label.casefold() not in {a.casefold() for a in aliases}:
            aliases.append(label)
    out["aliases"] = aliases
    hosts = [str(h) for h in out.get("careers_hosts") or () if str(h).strip()]
    for host in identity.careers_hosts:
        if host.lower() not in {h.lower() for h in hosts}:
            hosts.append(host.lower())
    out["careers_hosts"] = hosts
    return out
