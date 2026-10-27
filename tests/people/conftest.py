"""Shared builders for the people tests (synthetic data only)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from people.config import PeopleConfig, load_people_config
from people.models import Classification, Person

# The people golden oracle was written against the 2026-10-02 lane lexicon; the production
# lexicon follows the owner skill since 2026-10-24 (config/job_lanes.yaml), so these tests pin
# the frozen copy (tests/jobs/conftest.py does the same for the job pipeline).
FROZEN = Path(__file__).resolve().parents[1] / "fixtures/synthetic/jobs/config"


def frozen_people_config() -> PeopleConfig:
    """The people configuration over the frozen lexicon."""
    return load_people_config(FROZEN)


@pytest.fixture
def cfg() -> PeopleConfig:
    return frozen_people_config()


def person(key: str, status: str = "in_scope", **kw: Any) -> Person:
    """A Person row with a consistent classification for counts tests."""
    reason = kw.pop("reason", None)
    lanes = tuple(kw.pop("lanes", ()))
    priority = kw.pop("priority", "A" if status == "in_scope" else None)
    level = kw.pop("level", "director" if status in ("in_scope",) else None)
    if status in ("out_of_scope", "excluded", "review") and reason is None:
        reason = "synthetic_reason"
    cls = Classification(
        status=status,  # type: ignore[arg-type]
        reason=reason,
        level=level,
        lanes=lanes,
        priority=priority,
        band="senior" if level else None,
        rule_id="test",
    )
    defaults: dict[str, Any] = {
        "person_key": key,
        "source_record_id": f"rec-{key}",
        "origin": "title_search",
        "profile_url": None if status == "unresolved" else f"https://www.example.org/in/{key}",
        "classification": cls,
    }
    defaults.update(kw)
    return Person(**defaults)
