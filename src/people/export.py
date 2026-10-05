"""Read model for the People List and its Summary sheet (P16).

The list holds only ``in_scope`` people that are not erased and not internal-only (a person known
only from a provider whose output may never reach a hosted file). Excluded, review, unresolved,
merged and erased people never appear by name. The Summary sheet shows counts by status and
reason, never names, and no provider code, credit figure or call reference. Every text cell is
checked by the platform's client-output scanner before it is returned.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from account_intelligence.safety.client_output import assert_client_safe
from people.models import PeopleCounts, Person


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


@dataclass(frozen=True)
class PeopleExport:
    """The People List rows and the Summary sheet lines."""

    rows: tuple[PeopleListRow, ...]
    summary: tuple[tuple[str, str, int], ...]  # (status, reason, count)
    sentences: tuple[str, ...]


def build_export(
    people: Sequence[Person],
    counts: PeopleCounts,
    sentences: Sequence[str],
    source_codes: dict[str, str],
    erased: frozenset[str] = frozenset(),
) -> PeopleExport:
    """The client-facing read model; raises ``ClientOutputLeak`` on any denied term."""
    rows = []
    internal = 0
    for p in people:
        if p.status != "in_scope" or p.person_key in erased or not p.profile_url:
            continue
        if p.internal_only:
            internal += 1
            continue
        rows.append(
            PeopleListRow(
                name=p.name or "",
                title=p.title or "",
                level=(p.classification.level or "").replace("_", " "),
                lanes=", ".join(lane.replace("_", " ") for lane in p.classification.lanes),
                priority=p.classification.priority or "",
                profile_url=p.profile_url,
                source_code=source_codes.get(p.profile_url, ""),
            )
        )
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
    export = PeopleExport(tuple(rows), tuple(summary), tuple(sentences))
    texts = [cell for row in export.rows for cell in row.__dict__.values()]
    texts += [f"{s} {r}" for s, r, _ in export.summary]
    texts += list(export.sentences)
    assert_client_safe(texts)
    return export
