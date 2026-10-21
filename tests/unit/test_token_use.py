"""The token card's split of a run's model calls into the eight research steps (pure)."""

from __future__ import annotations

from views.token_use import estimate_key, split_steps


def test_token_use_splits_calls_into_the_eight_steps() -> None:
    """Each task kind lands in its step; extraction after the first sense check is follow-up
    work; an unknown kind is "Other model calls", listed only when it used tokens."""
    steps = split_steps(
        [
            ("abm_setup", False, 10),
            ("abm_pass1_extract", False, 100),
            ("abm_people_extract", False, 20),
            ("abm_sense_check", False, 7),
            ("abm_pass2_extract", True, 50),
            ("abm_distill", True, 30),
            ("plan_gaps", False, 3),
        ]
    )
    assert [(s.id, s.tokens) for s in steps] == [
        ("setup", 10),
        ("search", 0),
        ("read", 120),
        ("follow_up", 57),
        ("write", 30),
        ("build", 0),
        ("gate", 0),
        ("deliver", 0),
        ("other", 3),
    ]
    assert steps[3].name == "Follow-up searches"
    assert [s.id for s in split_steps([("abm_setup", False, 5)])][-1] == "deliver"
    assert split_steps([("abm_setup", False, -4)])[0].tokens == 0


def test_a_research_job_maps_to_its_usage_estimate() -> None:
    assert estimate_key("web_refresh", "jobs") == "jobs"
    assert estimate_key("scheduled_refresh", "people") == "people"
    assert estimate_key("new_research", "full") == "new_research"
    assert estimate_key("file_analysis", "file") == "file"
    assert estimate_key("file_analysis", "full") == "file_public"
    assert estimate_key("api_run", "full") is None
    assert estimate_key(None, None) is None
