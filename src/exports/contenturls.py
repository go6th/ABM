"""Every address of one snapshot made usable before it is mapped (review 2026-10-29, exports 2).

One posting link with a space in it, an upper-case scheme, ``javascript:``, ``ftp:`` or ``file:``
used to raise a validation error out of the mapper and lose all five files. ``clean_urls``
repairs what is only spelling (``account_intelligence.urls.clean_http_url``) and handles what
cannot be repaired row by row: a posting, a person or a detection with no usable address loses
the address and is withheld by its mapper (``bad_url`` in the manifest, counted); a LinkedIn
finding without one is left out and listed; a registered source whose address cannot be used is
left out of the registry view (its citations then fail loudly: corrupt provenance is never
hidden); an archived-copy address that is unusable is blanked (the row keeps its live link).
Pure; the content it returns is the only one the mappers read.
"""

from __future__ import annotations

import hashlib

from account_intelligence.urls import clean_http_url, normalize_url
from exports.content import SnapshotContent

# (where, row id, rule) of a row left out of its file, as ``MapContext.withhold`` records them
Dropped = tuple[str, str, str]


def _source_url(url: str) -> str | None:
    """A registered source's address: repaired, else its registry-normalised form."""
    fixed = clean_http_url(url)
    if fixed is not None:
        return fixed
    try:
        return clean_http_url(normalize_url(url))
    except ValueError:
        return None


def _archive(url: str) -> str:
    return clean_http_url(url) or ""


def clean_urls(
    content: SnapshotContent,
) -> tuple[SnapshotContent, tuple[Dropped, ...], frozenset[str]]:
    """``(content, dropped, bad_rows)``: the content with every address repaired or removed, the
    rows left out because of an address, and the ids of rows that kept no address (their
    mapper withholds them with the reason ``bad_url`` instead of ``missing_url``)."""
    dropped: list[Dropped] = []
    bad: set[str] = set()

    sources = []
    lost_codes: set[str] = set()
    for s in content.sources:
        url = _source_url(s.url)
        if url is None:
            lost_codes.add(s.code)
            dropped.append(("sources", s.code, "bad_url"))
            continue
        sources.append(s.model_copy(update={"url": url, "archive_url": _archive(s.archive_url)}))

    evidence = []
    for e in content.evidence:
        update: dict[str, object] = {}
        if e.url is not None:
            update["url"] = clean_http_url(e.url)
        if e.source_code in lost_codes:
            update["source_code"] = None
        evidence.append(e.model_copy(update=update) if update else e)

    people = []
    for p in content.people:
        profile = clean_http_url(p.profile_url)
        if profile is None:
            bad.add(p.id)
        found_via = tuple(
            v.model_copy(update={"mention_url": clean_http_url(v.mention_url)})
            if v.mention_url
            else v
            for v in p.found_via
        )
        people.append(
            p.model_copy(
                update={
                    "profile_url": profile or "",
                    "second_profile_url": clean_http_url(p.second_profile_url)
                    if p.second_profile_url
                    else None,
                    "found_via": found_via,
                }
            )
        )

    findings = []
    for f in content.findings:
        url = clean_http_url(f.url)
        if url is None:
            dropped.append(("summary.linkedin_findings", f.person_id, "bad_url"))
            continue
        findings.append(f.model_copy(update={"url": url}))

    postings = []
    for q in content.postings:
        posting_url = clean_http_url(q.url) if q.url else None
        if q.url and posting_url is None:
            bad.add(q.posting_key)
        postings.append(
            q.model_copy(update={"url": posting_url, "archive_url": _archive(q.archive_url)})
        )

    detections = []
    for d in content.detections:
        detection_url = clean_http_url(d.source_url) if d.source_url else None
        if d.source_url and detection_url is None:
            bad.add(d.id)
        detections.append(d.model_copy(update={"source_url": detection_url}))

    releases = []
    for rel in content.releases:
        release_url = clean_http_url(rel.url)
        if release_url is None:
            digest = hashlib.sha256(rel.url.encode()).hexdigest()[:8]
            dropped.append(("workspace.releases", digest, "bad_url"))
            continue
        releases.append(rel.model_copy(update={"url": release_url}))

    cleaned = content.model_copy(
        update={
            "releases": tuple(releases),
            "sources": tuple(sources),
            "evidence": tuple(evidence),
            "people": tuple(people),
            "findings": tuple(findings),
            "postings": tuple(postings),
            "detections": tuple(detections),
        }
    )
    return cleaned, tuple(dropped), frozenset(bad)
