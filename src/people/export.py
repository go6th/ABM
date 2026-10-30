"""Read model for the People List and its Summary sheet (P16).

The list holds only ``in_scope`` people that are not erased and not internal-only (a person known
only from a provider whose output may never reach a hosted file). Review, unresolved, merged and
erased people never appear by name. People excluded by a title rule are listed by name with their
title and the reason on the Summary sheet (owner's master prompt v2 section 9.3, 2026-10-24) and
nowhere else. The Summary sheet otherwise shows counts by status and reason, and no provider code,
credit figure or call reference. Every text cell is checked by the platform's client-output
scanner before it is returned.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from account_intelligence.safety.client_output import assert_client_safe, scan_text
from people.config import SiFirm
from people.models import PeopleCounts, Person, Profile
from people.si_firms import former_si_employers

# rule ids of the rubric's title exclusions (config/people_rubric.yaml ``exclusion_rules``)
TITLE_EXCLUSION_PREFIX = "excl."


def _clean_employers(employers: tuple[str, ...] | None) -> tuple[str, ...] | None:
    """Former employers without the ones the client-output scanner denies: the employer is left
    out of the cell, the person stays."""
    if employers is None:
        return None
    return tuple(e for e in employers if not scan_text(e))


@dataclass(frozen=True)
class FoundVia:
    """One way a person was found: the search kind, the title-search pass, the platform lane an
    association search ran for, and the research page a named person came from."""

    origin: str
    pass_no: int | None = None
    lane: str | None = None
    mention_url: str | None = None


def _found_via(person: Person) -> FoundVia:
    return FoundVia(person.origin, person.pass_no, person.lane_searched, person.mention_url)


@dataclass(frozen=True)
class PeopleListRow:
    """One People List row (client-facing)."""

    name: str
    title: str
    level: str
    lanes: str
    priority: str
    profile_url: str
    source_code: str
    origin: str = "title_search"
    # prior employers of the pulled profile; None when the profile gave no such field
    former_employers: tuple[str, ...] | None = None
    # the link of a merged duplicate kept beside the canonical profile
    second_profile_url: str | None = None
    # where the person is, as the pulled profile (or else the search record) gives it
    location: str | None = None
    # the People List's one role-family lane id (people_lanes), from the title alone; empty for a
    # record stored before the rule existed
    role_lane: str = ""
    # every search that found the person (the kept record and the duplicates merged into it)
    found_via: tuple[FoundVia, ...] = ()


@dataclass(frozen=True)
class PeopleExport:
    """The People List rows, the Summary sheet lines and the people excluded by title."""

    rows: tuple[PeopleListRow, ...]
    summary: tuple[tuple[str, str, int], ...]  # (status, reason, count)
    sentences: tuple[str, ...]
    excluded: tuple[tuple[str, str, str], ...] = ()  # (name, reason, title as reported)


def build_export(
    people: Sequence[Person],
    counts: PeopleCounts,
    sentences: Sequence[str],
    source_codes: dict[str, str],
    erased: frozenset[str] = frozenset(),
    profiles: Sequence[Profile] = (),
    si_firms: Sequence[SiFirm] = (),
) -> PeopleExport:
    """The client-facing read model; raises ``ClientOutputLeak`` on any denied term."""
    rows = []
    internal = 0
    by_profile = {pr.profile_url: pr for pr in profiles}
    locations = {pr.profile_url: pr.location for pr in profiles if pr.location}
    second: dict[str, str] = {}
    merged_in: dict[str, list[Person]] = {}
    for q in sorted(people, key=lambda q: q.profile_url or ""):
        if q.merged_into and q.profile_url and q.merged_into not in second:
            second[q.merged_into] = q.profile_url
        if q.merged_into:
            merged_in.setdefault(q.merged_into, []).append(q)
    for p in people:
        if p.status != "in_scope" or p.person_key in erased or not p.profile_url:
            continue
        if p.internal_only:
            internal += 1
            continue
        other = second.get(p.person_key)
        row = PeopleListRow(
            name=p.name or "",
            title=p.title or "",
            level=(p.classification.level or "").replace("_", " "),
            lanes=", ".join(lane.replace("_", " ") for lane in p.classification.lanes),
            priority=p.classification.priority or "",
            profile_url=p.profile_url,
            source_code=source_codes.get(p.profile_url, ""),
            origin=p.origin,
            former_employers=_clean_employers(
                former_si_employers(by_profile.get(p.profile_url), si_firms)
            ),
            second_profile_url=other if other and other != p.profile_url else None,
            location=locations.get(p.profile_url) or p.location or None,
            role_lane=p.classification.role_lane or "",
            found_via=tuple(
                dict.fromkeys(
                    _found_via(m)
                    for m in sorted(
                        (p, *merged_in.get(p.person_key, ())),
                        key=lambda m: (m.pass_no or 0, m.source_record_id),
                    )
                )
            ),
        )
        # found_via holds search kinds and a research page address that is not printed (only the
        # reference's wording of it is), so it is not scanned as a cell
        if any(scan_text(str(v)) for k, v in row.__dict__.items() if k != "found_via"):
            # a person whose own cells name a denied term (a data tool as their title or
            # employer) is kept off the file and counted, never the reason the set fails
            internal += 1
            continue
        rows.append(row)
    rows.sort(key=lambda r: (r.priority, r.name.casefold(), r.profile_url))
    summary: list[tuple[str, str, int]] = [("in scope", "", counts.in_scope)]
    if internal:
        summary.append(("in scope", "kept off this file by the distribution rules", internal))
    for status, block in (
        ("out of scope", counts.out_of_scope),
        ("excluded", counts.excluded),
        ("for review", counts.review),
    ):
        summary.extend((status, reason.replace("_", " "), n) for reason, n in block.items())
    summary.append(("no profile link yet", "", counts.unresolved))
    summary.append(("merged as duplicates", "", counts.merged_away))
    # only people excluded by a title rule are named (``excl.*`` in the rubric); a tenant's own
    # exclusion list and a record whose name is not a person are counted, never named. A title
    # the client-output scanner denies is left out, the name stays.
    excluded = tuple(
        sorted(
            (
                p.name,
                (p.classification.reason or "").replace("_", " "),
                "" if scan_text(p.title or "") else (p.title or ""),
            )
            for p in people
            if p.status == "excluded"
            and p.name
            # a name the client-output scanner denies is not listed (it used to fail the whole
            # build at the scan below); the person stays counted in the summary lines
            and not scan_text(p.name)
            and p.person_key not in erased
            and (p.classification.rule_id or "").startswith(TITLE_EXCLUSION_PREFIX)
        )
    )
    export = PeopleExport(tuple(rows), tuple(summary), tuple(sentences), excluded)
    texts = [str(v) for row in export.rows for k, v in row.__dict__.items() if k != "found_via"]
    texts += [f"{s} {r}" for s, r, _ in export.summary]
    texts += list(export.sentences)
    texts += [f"{n} {r} {t}" for n, r, t in excluded]
    assert_client_safe(texts)
    return export
