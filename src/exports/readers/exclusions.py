"""Tenant exclusions at render time (TN5): applied to a snapshot BEFORE it is mapped, so the export
files and the client tables (both built from ``exports.builder.map_snapshot``) leave out exactly
the same rows. Pull time is the people pipeline's own rule; this is the second check, and it
reads the tenant's CURRENT exclusions, not the version the run pinned when it started (leakage
review 2026-10-30, F1: an exclusion added after a run applies to every file built or served
after it; the owner's privacy-safe default). ``exclusion_hash`` names the set that was applied;
it is stored with every built file and every materialised table, and a file or table whose hash
differs from the tenant's current one is not served until it is rebuilt. Pure.

* an excluded person's row and LinkedIn findings are dropped, and their name is marked not
  listed, so the excluded-name guard keeps it out of every other sentence; a registered source
  that is the person's profile (or sits on an excluded company's domain) is dropped from the
  sources list, so it is also gone from ``build/urls.json``, and so is a detection's address;
* excluded companies are added to the run's forbidden terms, so any text naming them is refused
  by the same client-output rules that keep other accounts out (the build fails closed rather
  than print them).

GUARANTEE(test_exclusions_hold_at_pull_and_render_time)
GUARANTEE(test_an_exclusion_added_after_a_run_reaches_every_surface)
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from exports.content import SnapshotContent
from people.names import normalise_name
from tenancy.settings import ExclusionFilter

_SCHEME = re.compile(r"https?://(?:www\.)?", re.IGNORECASE)
# the hash of an empty exclusion set (a file built before the rule recorded none counts as this)
NO_EXCLUSIONS = "none"


def exclusion_hash(rule: ExclusionFilter) -> str:
    """16 hex characters naming the exact exclusion set (order independent), ``none`` for an
    empty one. A hash, not the list: it is stored beside the built files."""
    if not rule.people and not rule.companies:
        return NO_EXCLUSIONS
    blob = "\n".join(
        [*(f"p:{p}" for p in sorted(rule.people)), *(f"c:{c}" for c in sorted(rule.companies))]
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def apply_exclusions(content: SnapshotContent, rule: ExclusionFilter) -> SnapshotContent:
    """The snapshot without the tenant's excluded people, with its excluded companies forbidden."""
    if not rule.people and not rule.companies:
        return content  # nothing to apply; the caller marks the set (``exclusion_hash``)
    content = content.model_copy(update={"exclusion_hash": exclusion_hash(rule)})
    dropped = {
        p.id for p in content.people if rule.excludes_person(name=p.name, profile_url=p.profile_url)
    }
    names = {p.name for p in content.people if p.id in dropped}
    lost = {s.code for s in content.sources if text_excluded(rule, s.title, s.publisher, url=s.url)}
    return content.model_copy(
        update={
            "sources": tuple(s for s in content.sources if s.code not in lost),
            # evidence on an excluded address goes with its source; a detection keeps its row
            # but not the address
            "evidence": tuple(
                e
                for e in content.evidence
                if e.source_code not in lost and not (e.url and text_excluded(rule, url=e.url))
            ),
            "detections": tuple(
                d.model_copy(update={"source_url": None, "source_code": None})
                if d.source_url and text_excluded(rule, url=d.source_url)
                else d
                for d in content.detections
            ),
            # an archived copy that names an excluded person, or sits on an excluded company's
            # domain, is left out of the workspace zip as the client archive withholds it
            "releases": tuple(
                r for r in content.releases if not text_excluded(rule, r.title, r.text, url=r.url)
            ),
            "people": tuple(p for p in content.people if p.id not in dropped),
            "findings": tuple(f for f in content.findings if f.person_id not in dropped),
            "people_names": tuple(
                (n, listed and n not in names) for n, listed in content.people_names
            ),
            "forbidden_terms": tuple(sorted({*content.forbidden_terms, *rule.companies})),
        }
    )


def with_known_names(
    rule: ExclusionFilter, people: Iterable[tuple[str | None, str | None]]
) -> ExclusionFilter:
    """``rule`` plus the NAME of every person the rule already excludes by profile address or
    name, from the rows the research stored (``(name, profile_url)``): a free text that names an
    excluded person (a fact, a search, a source title) carries the name, not the address, so the
    text rule must know the names behind the addresses."""
    if not rule.people:
        return rule
    names = {
        f"name:{normalise_name(n)}"
        for n, url in people
        if n and normalise_name(n) and rule.excludes_person(name=n, profile_url=url)
    }
    return ExclusionFilter(rule.companies, frozenset(rule.people | names))


def text_excluded(rule: ExclusionFilter, *texts: str | None, url: str | None = None) -> bool:
    """True when any of ``texts`` names a person the tenant excluded (by name, or by profile URL)
    or ``url`` is on an excluded company's domain. The client archive uses it on every title,
    address and text it would show (security review 2026-10-29, F1): the people exclusion of the
    exports applies at render time to a snapshot, this applies it to free text. A name matches as
    whole words in the people pipeline's own normal form (``people.names``), a profile URL as the
    path of the address, so a page that mentions an excluded person is withheld whole. A text that
    names an excluded COMPANY is not hidden here: the company is a forbidden term (the caller
    passes ``rule.companies`` to the guard), as in the exports."""
    if not rule.people and not (rule.companies and url):
        return False
    if url:
        host = (urlsplit(url.strip()).hostname or "").lower().rstrip(".")
        if rule.excludes_company(host):
            return True
    names = [e[5:] for e in rule.people if e.startswith("name:") and e[5:]]
    urls = [e[4:] for e in rule.people if e.startswith("url:") and e[4:]]
    for raw in (*texts, url):
        if not raw:
            continue
        normal = f" {normalise_name(raw)} "
        if any(f" {n} " in normal for n in names):
            return True
        bare = _SCHEME.sub("", raw).lower()
        # the profile path, not a longer address that starts with it (``/in/jane-7`` is not
        # ``/in/jane-70``)
        if any(re.search(re.escape(u) + r"(?![\w-])", bare) for u in urls):
            return True
    return False
