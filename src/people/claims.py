"""Person claims templated by code from structured fields (P15).

One sourced claim per in-scope person: attribute ``people.<person_key>.title``, value the title as
the source returned it, evidence the canonical profile URL and the verbatim title string. A title
whose contact details were redacted at the boundary is skipped and counted. The pipeline never
sets a grade; a profile page is
not automatically primary (the grading engine decides).
"""

from __future__ import annotations

from collections.abc import Sequence

from account_intelligence.grading.schema import ProposedEvidence, ProposedSourced
from jobs.normalise import strip_pii
from people.models import Person

# The marker the shared redaction rule writes (taken from the rule, not retyped).
REDACTED = strip_pii("someone@example.com")


def title_claims(people: Sequence[Person]) -> tuple[list[ProposedSourced], int]:
    """Claims for in-scope people; returns ``(claims, skipped_for_contact_details)``."""
    claims: list[ProposedSourced] = []
    skipped = 0
    for p in people:
        if p.status != "in_scope" or not p.title or not p.profile_url:
            continue
        if REDACTED in p.title:  # contact details were redacted at the boundary
            skipped += 1
            continue
        claims.append(
            ProposedSourced(
                claim_kind="sourced",
                local_id=f"person-{p.person_key}",
                attribute_key=f"people.{p.person_key}.title",
                value=p.title,
                evidence=(ProposedEvidence(url=p.profile_url, supporting_sentence=p.title),),
                reasoning=(
                    f"Title as returned by a people source; level {p.classification.level},"
                    f" rule {p.classification.rule_id}."
                ),
            )
        )
    return claims, skipped
