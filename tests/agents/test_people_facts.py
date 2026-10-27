"""People facts from web pages (2026-10-27): an ``executive_role`` fact must name a person. A
value that is only a title ("Vice President of Technology Product Management") was shown on the
web People table as a person with no profile link; it is now dropped at extraction, counted."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from agents.people_names import names_a_person
from tests.agents import worlds
from tests.agents.conftest import Harness
from tests.agents.fakes import Page


@pytest.mark.parametrize(
    "value",
    [
        "Avery Quinn, Chief Information Officer",
        "Chief Information Officer Jane Doe",
        "Brian J. Blaser, President and Chief Executive Officer",
        "Jonathan Siegrist (CTO)",
        "José Müller - Head of Data",
        "Pieter van der Berg, CDO",
        "Łukasz Nowak, VP Engineering",
    ],
)
def test_values_that_name_a_person(value: str) -> None:
    assert names_a_person(value)


@pytest.mark.parametrize(
    "value",
    [
        "Vice President of Technology Product Management",
        "Chief Operations Officer",
        "SVP Global Sales",
        "Head of Supply Chain",
        "Director, Salesforce Platform",
        "VP, ServiceNow Practice",
        "CIO EMEA",
        "executive role",
    ],
)
def test_values_that_are_only_a_title(value: str) -> None:
    assert not names_a_person(value)


@pytest.mark.asyncio
async def test_executive_role_naming_nobody_is_dropped_and_counted(make) -> None:  # type: ignore[no-untyped-def]
    world = worlds.rich()
    d = world.domain
    world.pages["basic_fit_check"] = [
        Page(
            f"https://{d}/about",
            "exec: Avery Quinn, Chief Information Officer leads technology.\n"
            "role: Vice President of Technology Product Management owns the roadmap.",
        )
    ]
    h: Harness = make(world)
    quotes = {
        "Avery Quinn, Chief Information Officer": (
            "Avery Quinn, Chief Information Officer leads technology."
        ),
        "Vice President of Technology Product Management": (
            "Vice President of Technology Product Management owns the roadmap."
        ),
    }

    def executives(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        out = []
        for p in payload["payloads"]:
            for value, quote in quotes.items():
                if quote in p["text"]:
                    out.append(
                        {
                            "row_key": p["row_key"],
                            "attribute": "executive_role",
                            "value": value,
                            "origin": "keyword_match",
                            "evidence": [{"payload_ref": p["payload_ref"], "quote": quote}],
                        }
                    )
        return {"candidates": out}

    assert h.llm is not None
    for kind in ("abm_pass1_extract", "abm_pass2_extract"):
        h.llm.scripts[kind] = executives
    record, state = await h.run("r")
    values = {
        f.value
        for f in h.grading.facts_by_run[record.run_id].values()
        if f.attribute == "executive_role"
    }
    assert values == {"Avery Quinn, Chief Information Officer"}
    assert state["counters"]["drop_person_fact_without_name"] == 1
