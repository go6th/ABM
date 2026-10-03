"""Title search passes and the paging loop shared by every people source (P10, spec 7).

``plan_title_searches`` turns the configuration into (lane, pass) searches: the lane's configured
platform ownership and leadership titles, or, while those are not supplied, the proposal fallback
"<platform> <level word>" for the pass band. A lane that is off is recorded once as not searched;
a lane with no titles is recorded ``not_available_yet``, never searched with a guess.

``page_source`` pages ONE source from its cursor: it stores each page before using it (so a
resume continues from the stored cursor), drops a record id already seen, and stops on the first
of: no next cursor (``converged``), a page with no new person or a repeated cursor
(``no_progress``, reason ``no_new_people`` or ``cursor_repeat``), the page or people cap
(``cap_reached``), the budget or the deadline. It adds no retry; a failed call ends the source
with its ledger key recorded as a failed call.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from people.config import PeopleConfig
from people.models import PersonPage, RawPerson
from people.ports import BUDGET_REASONS, SourceUnavailable
from people.urls import canonical_profile_url

MAX_TITLES_PER_CALL = 20  # PeopleByTitleArgs: at most 20 titles per call


@dataclass(frozen=True)
class SearchSpec:
    """One planned search: a (lane, pass) title search or a lane's association search."""

    source_id: str
    lane: str
    pass_no: int  # 0 for the association search
    titles: tuple[str, ...] = ()
    titles_truncated: bool = False


@dataclass
class SourceOutcome:
    """What paging one source produced."""

    spec: SearchSpec
    records: list[RawPerson] = field(default_factory=list)
    call_refs: list[str] = field(default_factory=list)
    failed_refs: list[str] = field(default_factory=list)
    live_calls: int = 0  # pages fetched in this pass (not replayed from the store)
    pages: int = 0
    exit_reason: str = "converged"
    stop_detail: str | None = None
    cap_hit: bool = False
    complete: bool = True
    unavailable: str | None = None  # reason when the source could not be called at all


def titles_for(cfg: PeopleConfig, lane_id: str, band: str) -> tuple[str, ...]:
    """Titles of one (lane, band) search; empty when none are configured and no fallback."""
    configured = (*cfg.ownership_titles.get(lane_id, ()), *cfg.leadership_titles.get(lane_id, ()))
    if configured:
        return tuple(dict.fromkeys(configured))
    if cfg.title_fallback != "platform_and_level":
        return ()
    lane = next(lane for lane in cfg.lanes if lane.lane_id == lane_id)
    levels = [level for level in cfg.level_order if cfg.band_of_level.get(level) == band]
    words = [w for level in levels for w in cfg.level_words.get(level, ())]
    return tuple(dict.fromkeys(f"{p} {w}" for p in lane.platforms for w in words))


def plan_title_searches(
    cfg: PeopleConfig, lanes_off: frozenset[str]
) -> tuple[list[SearchSpec], dict[str, str]]:
    """The (lane, pass) searches to run and the lanes not searched with their reason."""
    specs: list[SearchSpec] = []
    not_searched: dict[str, str] = {}
    for lane in cfg.lanes:
        if lane.lane_id in lanes_off:
            not_searched[lane.lane_id] = "lane_not_in_scope"
            continue
        for pass_no, band in cfg.passes:
            titles = titles_for(cfg, lane.lane_id, band)
            if not titles:
                not_searched[lane.lane_id] = "not_available_yet"
                break
            specs.append(
                SearchSpec(
                    f"title:{lane.lane_id}:{pass_no}",
                    lane.lane_id,
                    pass_no,
                    titles[:MAX_TITLES_PER_CALL],
                    len(titles) > MAX_TITLES_PER_CALL,
                )
            )
    return specs, not_searched


def identity(record: RawPerson, cfg: PeopleConfig) -> str:
    """The identity a page is checked against for progress: canonical URL, else record id."""
    return canonical_profile_url(record.profile_url, cfg.profile_url) or record.source_record_id


PageCall = Callable[[str | None], Awaitable[PersonPage]]
SavePage = Callable[[int, str | None, PersonPage], Awaitable[None]]


async def page_source(
    spec: SearchSpec,
    call: PageCall,
    save: SavePage,
    cfg: PeopleConfig,
    *,
    stored: Sequence[tuple[int, str | None, PersonPage]],
    seen_records: set[str],
    seen_people: set[str],
    people_left: Callable[[], int],
    out_of_time: Callable[[], bool],
) -> SourceOutcome:
    """Page one source (see the module docstring). ``stored`` pages are replayed first."""
    out = SourceOutcome(spec)
    cursors: set[str | None] = set()
    cursor: str | None = None
    max_pages = cfg.cap("max_pages_per_source")
    page_no = 0
    stored_by_no = {no: (cur, page) for no, cur, page in stored}
    # LOOP(people search paging): cap=max_pages_per_source and max_people_per_run; exit=converged,
    # no_progress (no new person, cursor repeat, failed call), cap_reached, budget, deadline
    while True:
        if page_no >= max_pages:
            out.exit_reason, out.cap_hit, out.complete = "cap_reached", True, False
            break
        if people_left() <= 0:
            out.exit_reason, out.cap_hit, out.complete = "cap_reached", True, False
            out.stop_detail = "max_people_per_run"
            break
        replay = stored_by_no.get(page_no)
        if replay is not None:
            page = replay[1]
        else:
            if out_of_time():
                out.exit_reason, out.complete = "deadline", False
                break
            try:
                out.live_calls += 1
                page = await call(cursor)
            except SourceUnavailable as error:
                if error.call_ref:
                    out.failed_refs.append(error.call_ref)
                if page_no == 0 and not error.call_ref:
                    out.unavailable = error.reason
                out.exit_reason = "budget" if error.reason in BUDGET_REASONS else "no_progress"
                out.stop_detail, out.complete = error.reason, False
                break
            await save(page_no, cursor, page)
        cursors.add(cursor)
        page_no += 1
        out.pages = page_no
        if page.call_ref:
            out.call_refs.append(page.call_ref)
        out.complete = out.complete and page.complete
        new_people = 0
        for record in page.records:
            if record.source_record_id in seen_records:
                continue
            seen_records.add(record.source_record_id)
            out.records.append(record)
            who = identity(record, cfg)
            if who not in seen_people:
                seen_people.add(who)
                new_people += 1
        if page.next_cursor is None:
            out.exit_reason = "converged"
            break
        if new_people == 0:
            out.exit_reason, out.stop_detail, out.complete = "no_progress", "no_new_people", False
            break
        if page.next_cursor in cursors:
            out.exit_reason, out.stop_detail, out.complete = "no_progress", "cursor_repeat", False
            break
        cursor = page.next_cursor
    return out
