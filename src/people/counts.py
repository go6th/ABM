"""People counts computed from rows, with their bases (P6, P7, P8). Pure.

No function here takes a count as an argument: every figure is derived from ``Person``,
``Profile`` and ``Post`` rows. Invariants checked (``InvariantViolation`` when broken):

* ``found = distinct + merged_away`` and every merged row points at another row;
* ``distinct = in_scope + out_of_scope + excluded + review + unresolved``;
* ``tenure_known + tenure_unknown + date_invalid = profiles_read``;
* ``cert_field_provided + cert_field_absent = profiles_read``.

Per-lane figures are never summed into a distinct total (a person may be in two lanes). Unknown is
counted as unknown, never as zero: the alumni count is ``not computed`` without a reference list,
and "field not provided" is not "none listed". ``render_count_sentences`` writes sentences only
from these integers, each naming its base.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date

from people.alumni import matched_employer, reference_set
from people.certs import certified_lanes
from people.config import LaneTerms, TenureBucket
from people.errors import InvariantViolation
from people.models import AlumniCount, PeopleCounts, Person, Post, Profile
from people.tenure import compute_tenure

READ_STATES = ("read", "reused", "partial")


def _by_reason(people: Sequence[Person], status: str) -> dict[str, int]:
    counter = Counter(
        p.classification.reason or "unspecified" for p in people if p.status == status
    )
    return dict(sorted(counter.items()))


def profiles_read_of(
    people: Sequence[Person], profiles: Mapping[str, Profile]
) -> list[tuple[Person, Profile]]:
    """In-scope people with a read profile (the base of alumni, tenure and certifications)."""
    return [
        (p, profiles[p.person_key])
        for p in people
        if p.status == "in_scope" and p.profile_state in READ_STATES and p.person_key in profiles
    ]


def compute_counts(
    people: Sequence[Person],
    profiles: Mapping[str, Profile],
    posts: Sequence[Post],
    *,
    reference_employers: Sequence[str] | None,
    alumni_aliases: Mapping[str, Sequence[str]],
    as_of: date,
    buckets: Sequence[TenureBucket],
    lanes: Sequence[LaneTerms],
) -> PeopleCounts:
    """All people counts from rows (see the module docstring)."""
    keys = {p.person_key for p in people}
    if len(keys) != len(people):
        raise InvariantViolation("duplicate person keys")
    merged = [p for p in people if p.status == "merged"]
    for p in merged:
        if p.merged_into is None or p.merged_into not in keys or p.merged_into == p.person_key:
            raise InvariantViolation("a merged row must point at another row")
    for p in people:
        if p.status != "merged" and p.merged_into is not None:
            raise InvariantViolation("only merged rows point at another row")
    found, merged_away = len(people), len(merged)
    distinct_rows = [p for p in people if p.status != "merged"]
    distinct = len(distinct_rows)
    in_scope = sum(1 for p in distinct_rows if p.status == "in_scope")
    out, excl, review = (_by_reason(people, s) for s in ("out_of_scope", "excluded", "review"))
    unresolved = sum(1 for p in distinct_rows if p.status == "unresolved")
    if found != distinct + merged_away:
        raise InvariantViolation("found != distinct + merged_away")
    if distinct != in_scope + sum(out.values()) + sum(excl.values()) + sum(review.values()) + (
        unresolved
    ):
        raise InvariantViolation("distinct != sum of statuses")
    for p in distinct_rows:
        if p.status == "in_scope" and not p.profile_url:
            raise InvariantViolation("an in-scope person has no profile URL")
    read = profiles_read_of(people, profiles)
    requested = [
        p for p in distinct_rows if p.status == "in_scope" and p.profile_state != "not_requested"
    ]
    tenure_counter: Counter[str] = Counter()
    known = unknown = invalid = 0
    provided = absent = 0
    certified: Counter[str] = Counter()
    alumni_hits = 0
    refs = (
        reference_set(reference_employers, alumni_aliases) if reference_employers else frozenset()
    )
    for _, profile in read:
        verdict = compute_tenure(profile.join_date, profile.role_start, as_of, buckets)
        if verdict.status == "known":
            known += 1
            tenure_counter[verdict.bucket or ""] += 1
        elif verdict.status == "unknown":
            unknown += 1
        else:
            invalid += 1
        if profile.certifications is None:
            absent += 1
        else:
            provided += 1
            for lane in certified_lanes(profile.certifications, lanes):
                certified[lane] += 1
        if refs and matched_employer(profile.prior_employers or (), refs) is not None:
            alumni_hits += 1
    if known + unknown + invalid != len(read):  # pragma: no cover - one branch per profile
        raise InvariantViolation("tenure counts do not add up to profiles read")
    if provided + absent != len(read):  # pragma: no cover - one branch per profile
        raise InvariantViolation("certification counts do not add up to profiles read")
    alumni = (
        AlumniCount(computed=True, count=alumni_hits, base=len(read))
        if reference_employers
        else AlumniCount(computed=False, not_computed_reason="waiting_on_account_owner")
    )
    lane_counter: Counter[str] = Counter()
    for p in distinct_rows:
        if p.status == "in_scope":
            lane_counter.update(set(p.classification.lanes))
    priority_counter = Counter(
        p.classification.priority or "" for p in distinct_rows if p.status == "in_scope"
    )
    hostile = sum(1 for prof in profiles.values() if prof.hostile) + sum(
        1 for post in posts if post.hostile
    )
    dropped = sum(p.fields_dropped for p in people) + sum(
        prof.fields_dropped for prof in profiles.values()
    )
    return PeopleCounts(
        found=found,
        distinct=distinct,
        merged_away=merged_away,
        possible_duplicate=sum(1 for p in people if p.possible_duplicate),
        in_scope=in_scope,
        out_of_scope=out,
        excluded=excl,
        review=review,
        unresolved=unresolved,
        profiles_requested=len(requested),
        profiles_read=len(read),
        profiles_partial=sum(1 for p, _ in read if p.profile_state == "partial"),
        profiles_failed=sum(1 for p in requested if p.profile_state == "failed"),
        alumni=alumni,
        tenure_known=known,
        tenure_unknown=unknown,
        date_invalid=invalid,
        tenure_buckets={b.id: tenure_counter.get(b.id, 0) for b in buckets},
        cert_field_provided=provided,
        cert_field_absent=absent,
        certified_by_lane={lane.lane_id: certified.get(lane.lane_id, 0) for lane in lanes},
        posts_people=len({post.person_key for post in posts}),
        posts_pulled=len(posts),
        activity_unfiltered=sum(1 for p in distinct_rows if p.activity_unfiltered),
        hostile=hostile,
        fields_dropped=dropped,
        by_lane_in_scope={lane.lane_id: lane_counter.get(lane.lane_id, 0) for lane in lanes},
        by_priority={k: priority_counter.get(k, 0) for k in ("A", "B", "C")},
    )


def render_count_sentences(counts: PeopleCounts) -> tuple[str, ...]:
    """Plain sentences built only from ``counts``; each names its base. Configured labels
    (tenure band ids) are quoted so they are never read as figures."""
    lines = [
        f"{counts.found} people found, {counts.merged_away} merged as duplicates,"
        f" {counts.distinct} distinct.",
        f"{counts.in_scope} of {counts.distinct} distinct people are in scope.",
    ]
    for label, block in (
        ("out of scope", counts.out_of_scope),
        ("excluded", counts.excluded),
        ("for review", counts.review),
    ):
        for reason, n in block.items():
            lines.append(
                f"{n} of {counts.distinct} distinct people {label},"
                f" reason {reason.replace('_', ' ')}."
            )
    if counts.unresolved:
        lines.append(
            f"{counts.unresolved} of {counts.distinct} distinct people have no profile link yet."
        )
    lines.append(
        f"{counts.profiles_read} of {counts.profiles_requested} profiles requested were read"
        f" ({counts.profiles_partial} partly)."
    )
    if counts.alumni.computed:
        lines.append(
            f"{counts.alumni.count} of {counts.alumni.base} profiles read list a prior employer"
            " on the reference list."
        )
    else:
        lines.append("Alumni not counted, waiting on the account owner for the reference list.")
    for bucket, n in counts.tenure_buckets.items():
        lines.append(
            f"{n} of {counts.tenure_known} profiles with a dated tenure are in tenure band"
            f' "{bucket}".'
        )
    lines.append(
        f"{counts.tenure_unknown + counts.date_invalid} of {counts.profiles_read} profiles read"
        f" have no usable tenure ({counts.date_invalid} with an invalid date)."
    )
    for lane, n in counts.certified_by_lane.items():
        lines.append(
            f"{n} of {counts.cert_field_provided} profiles that list certifications hold one"
            f" for {lane.replace('_', ' ')}."
        )
    lines.append(
        f"{counts.cert_field_absent} of {counts.profiles_read} profiles read do not provide"
        " the certifications field."
    )
    return tuple(lines)
