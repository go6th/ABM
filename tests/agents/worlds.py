"""Three synthetic oracle accounts (all data is invented and labelled ``synthetic``).

* ``rich``: every source answers; one careers page; three senior people.
* ``sparse``: most sources return nothing; one source fails (Blocked with a reason).
* ``tangled``: contradictory headcounts, a subsidiary (entity boundary) and a similarly named
  organisation (adjacent organisation), and one recommended targeted pull.

The expected outcomes are written by hand in ``tests/agents/test_oracles.py``.
"""

from __future__ import annotations

from tests.agents.fakes import Page, World


def rich() -> World:
    d = "northwind-synthetic.example"
    return World(
        name="Northwind Synthetic",
        domain=d,
        pages={
            "company_news": [
                Page(
                    f"https://{d}/news/1",
                    "launch: Northwind opened a Salesforce centre of excellence in 2026.",
                )
            ],
            "earnings_filings": [
                Page(
                    f"https://{d}/ir/q2",
                    "revenue: Northwind reported revenue growth of 12 percent in Q2.",
                )
            ],
            "leadership_changes": [
                Page(
                    f"https://{d}/news/2",
                    "leadership: Northwind appointed a new chief digital officer in July.",
                )
            ],
            "job_board_by_lane": [
                Page(
                    f"https://{d}/careers",
                    "hiring: Northwind is hiring six MuleSoft integration engineers.",
                )
            ],
            "technology_detections": [
                Page(
                    f"https://{d}/tech",
                    "stack: Northwind runs Salesforce Service Cloud and MuleSoft.",
                )
            ],
            "basic_fit_check": [
                Page(
                    f"https://{d}/about",
                    "fit: Northwind employs about 5,000 people in field service.",
                )
            ],
            "people_by_platform": [
                Page(
                    f"https://{d}/people",
                    "person: Avery Quinn, Chief Information Officer at Northwind.\n"
                    "person: Rowan Hale, VP Salesforce Platform at Northwind.",
                )
            ],
            "full_profiles_senior_people": [
                Page(
                    f"https://{d}/people/a",
                    "person: Sam Ortiz, Director of Integration at Northwind.",
                )
            ],
        },
    )


def sparse() -> World:
    d = "quietfield-synthetic.example"
    return World(
        name="Quietfield Synthetic",
        domain=d,
        pages={
            "company_news": [
                Page(
                    f"https://{d}/news",
                    "launch: Quietfield announced a regional data platform pilot.",
                )
            ]
        },
        failing_rows={"earnings_filings"},
    )


def tangled() -> World:
    d = "meridian-synthetic.example"
    return World(
        name="Meridian Synthetic",
        domain=d,
        pages={
            "basic_fit_check": [
                Page(f"https://{d}/about", "headcount: Meridian employs 12,000 people worldwide.")
            ],
            "financial_baseline_verified": [
                Page(
                    f"https://{d}/ir",
                    "headcount: Meridian employs 8,500 people after the spin-off.",
                )
            ],
            "company_news": [
                Page(
                    f"https://{d}/news",
                    "entity: Meridian Labs, a subsidiary, signed a ServiceNow contract.",
                )
            ],
            "competitor_moves": [
                Page(
                    f"https://{d}/peer",
                    "peer: Meridian Health, not to be confused with Meridian, expanded SAP.",
                )
            ],
        },
        proposals=[
            {
                "row_key": "leadership_changes",
                "query": "Meridian chief information officer appointment",
                "reason": "hidden_title",
            }
        ],
    )
