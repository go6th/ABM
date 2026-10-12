"""Technologies per lane come from the account's stack, matched by the lane's name."""

from __future__ import annotations

from types import SimpleNamespace

from people.technology import lane_names, lane_terms, technologies_by_lane

NAMES = {
    "salesforce": "Salesforce ",
    "tableau_analytics": "Tableau and analytics",
    "data_ai": "Data and AI",
    "other": "Other",
}


def _t(name: str, slug: str, used: int) -> SimpleNamespace:
    return SimpleNamespace(technology_name=name, technology_slug=slug, job_post_used_count=used)


STACK = [
    _t("Salesforce", "salesforce", 9),
    _t("Salesforce Service Cloud", "salesforce-service-cloud", 4),
    _t("Tableau", "tableau", 7),
    _t("Google Analytics", "google-analytics", 12),
    _t("Workday", "workday", 20),
    _t("Kafka", "kafka", 3),
]


def test_lane_names_come_from_the_labels_without_the_parenthetical() -> None:
    assert lane_names()["salesforce"].strip() == "Salesforce"
    assert lane_terms("Tableau and analytics") == {"tableau", "analytics"}


def test_every_lane_takes_the_stack_technologies_carrying_its_name_most_used_first() -> None:
    out = technologies_by_lane(["salesforce", "tableau_analytics", "data_ai"], STACK, 10, NAMES)
    assert out == {
        "salesforce": ("salesforce", "salesforce-service-cloud"),
        "tableau_analytics": ("google-analytics", "tableau"),
        "data_ai": (),
    }


def test_the_catch_all_lane_takes_what_no_named_lane_took_and_the_limit_holds() -> None:
    out = technologies_by_lane(["other", "salesforce"], STACK, 2, NAMES)
    assert out["salesforce"] == ("salesforce", "salesforce-service-cloud")
    assert out["other"] == ("workday", "google-analytics")
    assert list(out) == ["other", "salesforce"]
