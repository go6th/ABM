"""The workbook Summary counts, computed in code from the bundle rows.

Every function here mirrors one formula block of a rendered workbook exactly, so the gate can
compare a recalculated workbook with the bundle and the web app can show the same numbers
without opening a file. The Excel semantics are kept on purpose: ``COUNTIF`` text criteria
compare without case, ``"*X*"`` is a substring match and ``"P*"`` a prefix match.

* Jobs Summary, per lane platform: ``COUNTIFS(Platforms named, "*lane*", Entity, "prefix*")``
  and the same narrowed by Status and by Class; then the three row totals (every row, rows
  whose Entity starts with the prefix, the rest).
* People Summary: people per level and priority A per level, their totals, Meets seniority
  rule equals Yes, and the lane table over senior people only (A, B, C, total per lane).
* Platform Ranking: share of base (``count / base``, 0 when the base is 0), the rank letter
  (A: share >= 0.15 and count >= 5; B: share >= 0.05 and count >= 3; C: count >= 2; else
  blank) and the Counts block (how many A, B and C).

``summary_values`` flattens all of it into one JSON-friendly mapping, the keys the manifest and
the workbook check share.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

Bundle = Mapping[str, Any]

LEVEL_LABELS: dict[int, str] = {
    1: "C-level, EVP and SVP",
    2: "VP",
    3: "Senior Director",
    4: "Director",
    5: "Associate Director",
    6: "Head",
    7: "Senior Manager",
    8: "Manager",
    9: "Lead, owner, specialist or developer",
}
CLASS_LABELS: dict[str, str] = {
    "build": "Build role",
    "admin": "Admin and owner role",
    "tool": "Working tool",
    "user": "User role",
}
PRIORITIES = ("A", "B", "C")
RANK_A = (0.15, 5)
RANK_B = (0.05, 3)
RANK_C_MIN = 2


@dataclass(frozen=True)
class JobsCount:
    """One lane row of the Jobs Summary."""

    total: int
    closed: int
    open: int
    build: int
    admin: int
    tool: int
    user: int


@dataclass(frozen=True)
class RowTotals:
    """The three row totals under the Jobs Summary table."""

    all_rows: int
    account_rows: int
    other_rows: int


@dataclass(frozen=True)
class LevelCount:
    """One level row of the People Summary."""

    people: int
    priority_a: int


@dataclass(frozen=True)
class LaneCount:
    """One lane row of the People Summary (senior people only)."""

    a: int
    b: int
    c: int
    total: int


@dataclass(frozen=True)
class RankRow:
    """One ranked platform as the Platform Ranking sheet computes it."""

    platform: str
    count: int
    open: int
    share: float
    rank: str


def _fold(value: object) -> str:
    return str(value if value is not None else "").casefold()


def platforms_named(row: Mapping[str, Any]) -> str:
    """The Platforms named cell: the row's platforms joined by comma and space."""
    return ", ".join(str(p) for p in (row.get("platforms") or ()))


def names_platform(row: Mapping[str, Any], platform: str) -> bool:
    """``COUNTIFS(..., "*platform*")``: the cell contains the word, any case."""
    return _fold(platform) in _fold(platforms_named(row))


def is_account_row(row: Mapping[str, Any], prefix: str) -> bool:
    """``COUNTIFS(Entity, "prefix*")``: the Entity cell starts with the prefix, any case."""
    return _fold(row.get("entity")).startswith(_fold(prefix))


def _prefix(bundle: Bundle) -> str:
    return str(bundle["meta"]["account_short"])


def _lanes(bundle: Bundle) -> list[str]:
    return [str(lane) for lane in bundle["meta"].get("lanes") or ()]


def jobs_summary(bundle: Bundle) -> dict[str, JobsCount]:
    """The Jobs Summary table: one row per lane platform, account positions only."""
    prefix = _prefix(bundle)
    rows = [r for r in bundle.get("jobs_rows") or () if is_account_row(r, prefix)]
    out: dict[str, JobsCount] = {}
    for lane in _lanes(bundle):
        lane_rows = [r for r in rows if names_platform(r, lane)]

        def count(field: str, value: str, lane_rows: list[Any] = lane_rows) -> int:
            return sum(1 for r in lane_rows if _fold(r.get(field)) == _fold(value))

        out[lane] = JobsCount(
            total=len(lane_rows),
            closed=count("status", "Closed"),
            open=count("status", "Open"),
            build=count("cls", CLASS_LABELS["build"]),
            admin=count("cls", CLASS_LABELS["admin"]),
            tool=count("cls", CLASS_LABELS["tool"]),
            user=count("cls", CLASS_LABELS["user"]),
        )
    return out


def jobs_row_totals(bundle: Bundle) -> RowTotals:
    """``COUNTA(Title)``, ``COUNTIF(Entity, "prefix*")`` and the difference."""
    prefix = _prefix(bundle)
    rows = list(bundle.get("jobs_rows") or ())
    all_rows = sum(1 for r in rows if str(r.get("title") or "") != "")
    account_rows = sum(1 for r in rows if is_account_row(r, prefix))
    return RowTotals(all_rows, account_rows, all_rows - account_rows)


def level_label(level: object) -> str:
    """The Level cell for a numeric level (an unknown level prints as its number)."""
    try:
        return LEVEL_LABELS.get(int(str(level)), str(level))
    except ValueError:
        return str(level)


def people_level_counts(bundle: Bundle) -> dict[str, LevelCount]:
    """People per level and priority A per level, in the sheet's level order."""
    rows = list(bundle.get("people_rows") or ())
    out: dict[str, LevelCount] = {}
    for label in LEVEL_LABELS.values():
        at_level = [r for r in rows if _fold(level_label(r.get("level"))) == _fold(label)]
        out[label] = LevelCount(
            people=len(at_level),
            priority_a=sum(1 for r in at_level if _fold(r.get("priority")) == "a"),
        )
    return out


def people_level_totals(bundle: Bundle) -> LevelCount:
    """The Total row under the level table (``SUM`` of the column)."""
    counts = people_level_counts(bundle).values()
    return LevelCount(sum(c.people for c in counts), sum(c.priority_a for c in counts))


def meets_seniority(bundle: Bundle) -> int:
    """``COUNTIF(Meets seniority rule, "Yes")``."""
    return sum(1 for r in bundle.get("people_rows") or () if bool(r.get("senior")))


def people_lanes(bundle: Bundle) -> list[str]:
    """The lane labels of the People Summary: ``meta.people_lanes``, else the distinct lanes
    of the rows in order of first appearance."""
    lanes = [str(lane) for lane in bundle["meta"].get("people_lanes") or ()]
    if lanes:
        return lanes
    seen: dict[str, None] = {}
    for r in bundle.get("people_rows") or ():
        seen.setdefault(str(r.get("lane") or ""), None)
    return [lane for lane in seen if lane]


def people_lane_counts(bundle: Bundle) -> dict[str, LaneCount]:
    """``COUNTIFS(Lane, lane, Priority, p, Meets seniority rule, "Yes")`` per lane and priority,
    with the row total."""
    senior = [r for r in bundle.get("people_rows") or () if bool(r.get("senior"))]
    out: dict[str, LaneCount] = {}
    for lane in people_lanes(bundle):
        in_lane = [r for r in senior if _fold(r.get("lane")) == _fold(lane)]
        a, b, c = (
            sum(1 for r in in_lane if _fold(r.get("priority")) == p.casefold()) for p in PRIORITIES
        )
        out[lane] = LaneCount(a, b, c, a + b + c)
    return out


def people_lane_totals(bundle: Bundle) -> LaneCount:
    """The Total row under the lane table."""
    counts = people_lane_counts(bundle).values()
    return LaneCount(
        sum(x.a for x in counts),
        sum(x.b for x in counts),
        sum(x.c for x in counts),
        sum(x.total for x in counts),
    )


def share_of(count: int, base: int) -> float:
    """``IF(base=0, 0, count/base)``."""
    return 0.0 if base == 0 else count / base


def rank_of(count: int, share: float) -> str:
    """The rank formula: A, B, C or blank."""
    if share >= RANK_A[0] and count >= RANK_A[1]:
        return "A"
    if share >= RANK_B[0] and count >= RANK_B[1]:
        return "B"
    if count >= RANK_C_MIN:
        return "C"
    return ""


def platform_ranking(bundle: Bundle) -> list[RankRow]:
    """Every ranked row with its share and rank, in sheet order."""
    ranking = bundle.get("ranking") or {}
    base = int(ranking.get("base") or 0)
    out = []
    for row in ranking.get("rows") or ():
        count = int(row.get("count") or 0)
        share = share_of(count, base)
        out.append(
            RankRow(
                str(row.get("platform")),
                count,
                int(row.get("open") or 0),
                share,
                rank_of(count, share),
            )
        )
    return out


def rank_counts(bundle: Bundle) -> dict[str, int]:
    """The Counts block: ``COUNTIF(Rank, "A")`` and the same for B and C."""
    rows = platform_ranking(bundle)
    return {letter: sum(1 for r in rows if r.rank == letter) for letter in PRIORITIES}


def summary_values(bundle: Bundle) -> dict[str, int | float | str]:
    """Every Summary value as one flat mapping (``jobs.<lane>.total``, ``jobs.rows.all``,
    ``people.level.<label>.people``, ``people.senior``, ``people.lane.<lane>.a``,
    ``ranking.<platform>.rank`` and ``.share``, ``ranking.count.A``)."""
    out: dict[str, int | float | str] = {}
    for lane, jc in jobs_summary(bundle).items():
        for key, value in asdict(jc).items():
            out[f"jobs.{lane}.{key}"] = value
    totals = jobs_row_totals(bundle)
    out["jobs.rows.all"] = totals.all_rows
    out["jobs.rows.account"] = totals.account_rows
    out["jobs.rows.other"] = totals.other_rows
    for label, lc in people_level_counts(bundle).items():
        out[f"people.level.{label}.people"] = lc.people
        out[f"people.level.{label}.priority_a"] = lc.priority_a
    level_totals = people_level_totals(bundle)
    out["people.level.total.people"] = level_totals.people
    out["people.level.total.priority_a"] = level_totals.priority_a
    out["people.senior"] = meets_seniority(bundle)
    for lane, lane_count in people_lane_counts(bundle).items():
        for key, value in asdict(lane_count).items():
            out[f"people.lane.{lane}.{key}"] = value
    lane_totals = people_lane_totals(bundle)
    for key, value in asdict(lane_totals).items():
        out[f"people.lane.total.{key}"] = value
    out["ranking.base"] = int((bundle.get("ranking") or {}).get("base") or 0)
    for row in platform_ranking(bundle):
        out[f"ranking.{row.platform}.count"] = row.count
        out[f"ranking.{row.platform}.share"] = row.share
        out[f"ranking.{row.platform}.rank"] = row.rank
    for letter, n in rank_counts(bundle).items():
        out[f"ranking.count.{letter}"] = n
    return out


def lane_sequence(bundle: Bundle) -> Sequence[str]:
    """The lane platforms in print order (a convenience for callers that only need the keys)."""
    return tuple(_lanes(bundle))
