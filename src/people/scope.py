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
6. ``review(level_unknown)`` when no level cue matches; ``out_of_scope(below_scope)`` when the
   level is outside the scope.
7. With a narrowed geography: ``review(location_unknown)`` or ``out_of_scope(outside_geography)``.
8. ``review(exclusion_ambiguous)`` on a review cue.
9. ``review(priority_unmatched)`` when no priority rule matches; otherwise ``in_scope`` with
   level, lanes and priority.

Every outcome carries the rule id and the matching substring or field value as evidence. No model
decides any of this.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from people.config import PeopleConfig
from people.cues import clean, find_cue
from people.exclusion import check_exclusion
from people.lane import lanes_for
from people.level import classify_level
from people.models import Classification
from people.priority import assign_priority


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


def _same_org(domain: str | None, account_domain: str) -> bool:
    if not domain:
        return False
    d = domain.strip().lower().removeprefix("www.")
    return d == account_domain or d.endswith("." + account_domain)


def classify(
    person: ScopeInput, cfg: PeopleConfig, account_domain: str, regions: Sequence[str] = ()
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
    if person.employer_domain and not _same_org(person.employer_domain, account_domain):
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
        return Classification(status="review", reason="level_unknown", rule_id="scope.level")
    lanes = tuple(m.lane for m in lanes_for(person.title, person.headline, cfg.lanes))
    if level.level not in cfg.levels_in_scope:
        return Classification(
            status="out_of_scope",
            reason="below_scope",
            rule_id=level.rule_id,
            evidence=level.evidence,
            level=level.level,
            level_rule=level.rule_id,
            lanes=lanes,
        )
    common = {
        "level": level.level,
        "level_rule": level.rule_id,
        "band": cfg.band_of_level[level.level],
        "lanes": lanes,
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
    priority = assign_priority(cfg.band_of_level[level.level], bool(lanes), cfg.priority_rules)
    if priority is None:
        return Classification(
            status="review", reason="priority_unmatched", rule_id="scope.priority", **common
        )
    return Classification(
        status="in_scope",
        rule_id=level.rule_id,
        evidence=level.evidence,
        priority=priority.priority,
        priority_rule=priority.rule_id,
        **common,
    )
