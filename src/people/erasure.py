"""Tombstone erasure (D-23; NOT exposed through the API until counsel decides, D-06).

Erasure keeps a person's identity (``person_key``), status and merge chain, and nulls every
personal field. ``erase_person`` is the pure rule; ``people.repo.PgPeopleRepo.erase`` applies it
in one transaction to the people row and the person's profiles and posts, with ``erased_at`` and a
reason, and records an event. Nothing is deleted.
"""

from __future__ import annotations

from people.models import Person, Post, Profile

PERSON_FIELDS = (
    "name",
    "title",
    "headline",
    "profile_url_raw",
    "location",
    "mention_sentence",
)
PROFILE_FIELDS = (
    "name",
    "current_title",
    "headline",
    "employer",
    "prior_employers",
    "employments",
    "join_date",
    "role_start",
    "certifications",
    "location",
    "own_description",
    "own_description_sha256",
    "last_activity_at",
)


def erase_person(person: Person) -> Person:
    """The person with every personal field nulled; key, status and merge chain kept."""
    return person.model_copy(update=dict.fromkeys(PERSON_FIELDS))


def erase_profile(profile: Profile) -> Profile:
    """The profile with every allow-listed personal field nulled."""
    return profile.model_copy(update=dict.fromkeys(PROFILE_FIELDS))


def erase_post(post: Post) -> Post:
    """The post with its excerpt removed."""
    return post.model_copy(update={"excerpt": ""})
