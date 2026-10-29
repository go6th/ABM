"""Full profiles: the allow-list boundary (P9) and the pull-once profile loop (P10, spec 7).

``from_fields`` keeps only the configured business fields (D-23) and counts the rest as dropped;
contact details, photos, personal links and anything unlisted never pass. ``to_profile`` types the
kept fields without guessing: a value of the wrong type is dropped (and the profile marked
partial), a date keeps its precision, ``certifications`` absent stays ``None`` (field not
provided) while an empty list stays ``()``. The person's own description is sanitised, capped and
has email addresses and phone numbers replaced (they never reach a column);
a description carrying the canary or an instruction marks the profile ``hostile`` and is not kept.

``pull_profiles`` reads every target once: a profile already stored for the account (any run) is
reused without a call; one person's failure is recorded on that person and never aborts the
batch; the loop stops on the profile cap, the budget or the deadline. Profiles are stored per
batch, so a resume never pays for a profile twice.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from jobs.normalise import strip_pii
from people.config import PeopleConfig
from people.cues import clean
from people.models import Employment, PartialDate, Person, Profile, RawProfile
from people.ports import BUDGET_REASONS, ProfilePort, SourceUnavailable
from people.urls import canonical_profile_url

_STR_FIELDS = ("name", "current_title", "headline", "employer", "location")


def from_fields(
    fields: Mapping[str, object], allowed: frozenset[str]
) -> tuple[dict[str, object], int]:
    """Keep allow-listed keys; return them and the number of dropped keys."""
    kept = {k: v for k, v in fields.items() if k in allowed}
    return kept, len(fields) - len(kept)


def _str_list(value: object, max_chars: int) -> tuple[str, ...] | None:
    if not isinstance(value, list | tuple) or not all(isinstance(v, str) for v in value):
        return None
    return tuple(c for c in (clean(v, max_chars) for v in value) if c)


def _year_or_date(value: object) -> PartialDate | None:
    """A year given as a number or a text date (``2019``, ``2019-03``) as a ``PartialDate``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return PartialDate.parse(f"{value:04d}")
    return PartialDate.parse(value)


def _employments(value: Sequence[object], max_chars: int) -> tuple[tuple[Employment, ...], bool]:
    """The dated experience entries of a profile and whether any entry was unusable. An entry is
    a mapping with a text ``employer`` and optional ``start`` and ``end`` (a missing end means to
    date); an entry without an employer, or with a date that is not a date, is dropped (the
    profile is then marked partial). Nothing is guessed."""
    out: list[Employment] = []
    bad = False
    for item in value:
        if not isinstance(item, Mapping) or not isinstance(item.get("employer"), str):
            bad = True
            continue
        employer = clean(item["employer"], max_chars)
        start, end = item.get("start"), item.get("end")
        parsed_start, parsed_end = _year_or_date(start), _year_or_date(end)
        if not employer or (start is not None and parsed_start is None):
            bad = True
            continue
        if end is not None and parsed_end is None:
            bad = True
            continue
        out.append(Employment(employer=employer, start=parsed_start, end=parsed_end))
    return tuple(out), bad


def to_profile(
    raw: RawProfile,
    person_key: str,
    cfg: PeopleConfig,
    *,
    is_hostile: Callable[[str], bool],
) -> Profile:
    """A typed, allow-listed ``Profile`` (see the module docstring)."""
    kept, dropped = from_fields(raw.fields, cfg.allowed_profile_fields)
    title_cap, text_cap = cfg.cap("max_title_chars"), cfg.cap("max_text_chars")
    partial = not raw.complete
    values: dict[str, Any] = {}
    for name in _STR_FIELDS:
        value = kept.get(name)
        if value is None:
            continue
        if isinstance(value, str):
            values[name] = clean(value, title_cap) or None
        else:
            partial, dropped = True, dropped + 1
    for name in ("prior_employers", "certifications"):
        if name in kept:
            parsed = _str_list(kept[name], title_cap)
            if parsed is None:
                partial, dropped = True, dropped + 1
            else:
                values[name] = parsed
    if "employments" in kept:
        if isinstance(kept["employments"], list | tuple):
            entries, bad = _employments(kept["employments"], title_cap)
            values["employments"] = entries
        else:  # the wrong type: the field counts as not provided
            bad = True
        if bad:
            partial, dropped = True, dropped + 1
    for name in ("join_date", "role_start"):
        if kept.get(name) is not None:
            parsed_date = PartialDate.parse(kept[name])
            if parsed_date is None:
                partial = True
            values[name] = parsed_date
    activity = PartialDate.parse(kept.get("last_activity_at"))
    if activity is not None and activity.precision == "day":
        values["last_activity_at"] = activity.earliest()
    hostile = False
    description = kept.get("own_description")
    if isinstance(description, str) and description.strip():
        text = strip_pii(clean(description, text_cap))
        if is_hostile(text):
            hostile = True
        else:
            values["own_description"] = text
            values["own_description_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    url = canonical_profile_url(raw.profile_url, cfg.profile_url) or raw.profile_url
    return Profile(
        person_key=person_key,
        profile_url=url,
        provider_code=raw.provider_code,
        raw_ref=raw.raw_ref,
        call_ref=raw.call_ref,
        fields_dropped=dropped,
        internal_only=raw.provider_code in cfg.internal_only_providers,
        partial=partial,
        hostile=hostile,
        **values,
    )


@dataclass
class ProfileOutcome:
    """Profiles by canonical URL, per-person state and the loop exit."""

    profiles: dict[str, Profile] = field(default_factory=dict)
    state: dict[str, tuple[str, str | None]] = field(default_factory=dict)  # key -> (state, why)
    call_refs: list[str] = field(default_factory=list)
    failed_refs: list[str] = field(default_factory=list)
    pulled: int = 0
    exit_reason: str = "converged"
    cap_hit: bool = False


async def pull_profiles(
    targets: Sequence[Person],
    port: ProfilePort,
    stored: Mapping[str, Profile],
    save: Callable[[Sequence[Profile]], Awaitable[None]],
    cfg: PeopleConfig,
    *,
    is_hostile: Callable[[str], bool],
    out_of_time: Callable[[], bool],
) -> ProfileOutcome:
    """One pull per target at most (see the module docstring)."""
    out = ProfileOutcome()
    batch_size = max(cfg.cap("profile_batch_size"), 1)
    cap = cfg.cap("max_profiles_per_run")
    stopped: str | None = None
    # LOOP(profile batches): cap=max_profiles_per_run, profile_batch_size per batch; exit=converged,
    # cap_reached, budget, deadline (one person's failure is isolated)
    for start in range(0, len(targets), batch_size):
        batch: list[Profile] = []
        for person in targets[start : start + batch_size]:
            url = person.profile_url or ""
            if url in stored:
                out.profiles[url] = stored[url].model_copy(update={"person_key": person.person_key})
                out.state[person.person_key] = ("reused", None)
                continue
            if stopped is None and out.pulled >= cap:
                stopped, out.cap_hit = "cap_reached", True
            if stopped is None and out_of_time():
                stopped = "deadline"
            if stopped is not None:
                out.state[person.person_key] = ("cap", stopped)
                continue
            out.pulled += 1
            try:
                raw = await port.pull(url)
            except SourceUnavailable as error:
                if error.call_ref:
                    out.failed_refs.append(error.call_ref)
                out.state[person.person_key] = ("failed", error.reason)
                if error.reason in BUDGET_REASONS:
                    stopped = "budget" if error.reason == "budget_cap" else "deadline"
                continue
            out.call_refs.append(raw.call_ref)
            profile = to_profile(raw, person.person_key, cfg, is_hostile=is_hostile)
            out.profiles[url] = profile
            out.state[person.person_key] = ("partial" if profile.partial else "read", None)
            batch.append(profile)
        if batch:
            await save(batch)
    out.exit_reason = stopped or "converged"
    return out


def recent(last_activity: date | None, as_of: date, days: int) -> bool | None:
    """True when the last activity is within ``days`` of ``as_of``; ``None`` when unknown."""
    if last_activity is None:
        return None
    return 0 <= (as_of - last_activity).days <= days
