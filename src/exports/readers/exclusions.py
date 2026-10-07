"""Tenant exclusions at render time (TN5): applied to a snapshot BEFORE it is mapped, so the export
files and the client tables (both built from ``exports.builder.map_snapshot``) leave out exactly
the same rows. Pull time is the people pipeline's own rule; this is the second check, for a run
read after the tenant excluded someone. Pure.

* an excluded person's row and LinkedIn findings are dropped, and their name is marked not
  listed, so the excluded-name guard keeps it out of every other sentence;
* excluded companies are added to the run's forbidden terms, so any text naming them is refused
  by the same client-output rules that keep other accounts out (the build fails closed rather
  than print them).

GUARANTEE(test_exclusions_hold_at_pull_and_render_time)
"""

from __future__ import annotations

from exports.content import SnapshotContent
from tenancy.settings import ExclusionFilter


def apply_exclusions(content: SnapshotContent, rule: ExclusionFilter) -> SnapshotContent:
    """The snapshot without the tenant's excluded people, with its excluded companies forbidden."""
    if not rule.people and not rule.companies:
        return content
    dropped = {
        p.id for p in content.people if rule.excludes_person(name=p.name, profile_url=p.profile_url)
    }
    names = {p.name for p in content.people if p.id in dropped}
    return content.model_copy(
        update={
            "people": tuple(p for p in content.people if p.id not in dropped),
            "findings": tuple(f for f in content.findings if f.person_id not in dropped),
            "people_names": tuple(
                (n, listed and n not in names) for n, listed in content.people_names
            ),
            "forbidden_terms": tuple(sorted({*content.forbidden_terms, *rule.companies})),
        }
    )
