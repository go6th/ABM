"""One status per found person from deterministic rules (P4, P5). Pure.

Order (the first that applies decides; exclusion beats inclusion):

1. ``merged`` when the record points at a canonical record.
2. ``out_of_scope(not_current)`` when the source says the person is not current, or names a
   current employer domain that is not the account's.
3. ``excluded(reason)`` on an exclusion cue in the title.
4. ``unresolved`` when there is no canonical profile URL (P2).
5. ``review(employment_unknown)`` when nothing shows the person is current at the account (no
   current flag, no employer domain, and the record did not come from a search restricted to the
   account domain).
6. The level: ``review(level_unknown)`` only for a person with no title at all; a title with no
   level cue is the default level (``other``, "Lead, owner, specialist or developer"). A level
   below the seniority rule (band ``below``) stays on the list as ``in_scope`` only when the title
   names a platform or technology (``people_stack_terms`` and the lane lexicon), else
   ``out_of_scope(below_scope)``. The level is read from the title; the profile adds nothing.
7. With a narrowed geography: ``review(location_unknown)`` or ``out_of_scope(outside_geography)``.
8. ``review(exclusion_ambiguous)`` on a review cue.
9. ``in_scope`` with level, platform lanes, the People List's one role lane and the priority,
   both from the title alone (the priority is never unmatched: the rubric has a default).

Every outcome carries the rule id and the matching substring or field value as evidence. No model
decides any of this.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from people.config import PeopleConfig
from people.cues import clean, find_cue
from people.exclusion import check_exclusion
from people.lane import lanes_for, role_lane_for
from people.level import LevelMatch, classify_level
from people.models import Classification
from people.priority import priority_for_title


@dataclass(frozen=True)
class ScopeInput:
    """The fields the scope rules read for one person."""

    merged: bool
    has_url: bool
    title: str | None
    headline: str | None
    employer_domain: str | None
    is_current: bool | None
    search_domain: str | None
    location: str | None


def _same_org(domain: str | None, account_domain: str, allowed: Sequence[str] = ()) -> bool:
    if not domain:
        return False
    d = domain.strip().lower().removeprefix("www.")
    owned = (account_domain, *(a.lower() for a in allowed if a))
    return any(d == a or d.endswith("." + a) for a in owned)


def names_stack(title: str | None, cfg: PeopleConfig) -> str | None:
    """The first platform or technology word the TITLE names (the lane lexicon's platform names
    and aliases, then ``people_stack_terms``), or ``None``. The headline does not count: the
    owner's rule is that the title names it."""
    text = clean(title)
    for lane in cfg.lanes:
        hit = find_cue(text, lane.terms)
        if hit is not None:
            return hit
    return find_cue(text, cfg.stack_terms)


def classify(
    person: ScopeInput,
    cfg: PeopleConfig,
    account_domain: str,
    regions: Sequence[str] = (),
    allowed_domains: Sequence[str] = (),
) -> Classification:
    """The one status for ``person`` (see the module docstring)."""
    if person.merged:
        return Classification(status="merged", rule_id="merge")
    if person.is_current is False:
        return Classification(
            status="out_of_scope",
            reason="not_current",
            rule_id="scope.not_current",
            evidence="is_current=false",
        )
    if person.employer_domain and not _same_org(
        person.employer_domain, account_domain, allowed_domains
    ):
        return Classification(
            status="out_of_scope",
            reason="not_current",
            rule_id="scope.other_employer",
            evidence=f"employer_domain={clean(person.employer_domain, 253)}",
        )
    verdict = check_exclusion(person.title, cfg.exclusion_rules, cfg.review_rules)
    if verdict.outcome == "excluded":
        return Classification(
            status="excluded",
            reason=verdict.reason,
            rule_id=verdict.rule_id,
            evidence=verdict.evidence,
        )
    if not person.has_url:
        return Classification(status="unresolved", rule_id="scope.no_profile_url")
    current = (
        person.is_current is True
        or _same_org(person.employer_domain, account_domain)
        or person.search_domain == account_domain
    )
    if not current:
        return Classification(
            status="review", reason="employment_unknown", rule_id="scope.employment_unknown"
        )
    level = classify_level(person.title, cfg.level_rules, cfg.level_order)
    if level is None:
        if not clean(person.title):
            return Classification(status="review", reason="level_unknown", rule_id="scope.level")
        level = LevelMatch(cfg.default_level, cfg.default_level_rule, "")
    lanes = tuple(m.lane for m in lanes_for(person.title, person.headline, cfg.lanes))
    band = cfg.band_of_level[level.level]
    stack: str | None = None
    if level.level not in cfg.levels_in_scope:
        stack = names_stack(person.title, cfg)
        if stack is None:
            return Classification(
                status="out_of_scope",
                reason="below_scope",
                rule_id=level.rule_id,
                evidence=level.evidence or None,
                level=level.level,
                level_rule=level.rule_id,
                band=band,
                lanes=lanes,
            )
    lane = role_lane_for(
        person.title, cfg.role_lanes, cfg.role_lane_default, cfg.role_lane_default_rule
    )
    common = {
        "level": level.level,
        "level_rule": level.rule_id,
        "band": band,
        "lanes": lanes,
        "role_lane": lane.lane,
        "role_lane_rule": lane.rule_id,
    }
    if regions:
        where = clean(person.location)
        if not where:
            return Classification(
                status="review", reason="location_unknown", rule_id="scope.geography", **common
            )
        hit = find_cue(where, [r.casefold() for r in regions])
        if hit is None:
            return Classification(
                status="out_of_scope",
                reason="outside_geography",
                rule_id="scope.geography",
                evidence=where[:200],
                **common,
            )
    if verdict.outcome == "review":
        return Classification(
            status="review",
            reason=verdict.reason,
            rule_id=verdict.rule_id,
            evidence=verdict.evidence,
            **common,
        )
    priority = priority_for_title(
        person.title, cfg.priority_rules, cfg.priority_default, cfg.priority_default_rule
    )
    return Classification(
        status="in_scope",
        rule_id=level.rule_id,
        evidence=stack or level.evidence or None,
        priority=priority.priority,
        priority_rule=priority.rule_id,
        **common,
    )
