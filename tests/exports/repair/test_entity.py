"""Entity resolution of a posting: profile rules, other-employer wording, host rules, collisions.
Two invented accounts prove nothing in the module is tied to one of them."""

from __future__ import annotations

from datetime import date

import pytest

from exports.repair.core import NOT_ACCOUNT, EntityRule, WorkspaceProfile
from exports.repair.entity import (
    account_registrables,
    is_account_host,
    is_domain_collision,
    is_job_board,
    tag_entity,
)


def zephyr() -> WorkspaceProfile:
    return WorkspaceProfile(
        account_name="Zephyr Dynamics Corporation",
        account_short="Zephyr",
        domain="zephyrdyn.com",
        run_date=date(2026, 10, 19),
        window_from=date(2025, 10, 19),
        window_to=date(2026, 10, 19),
        domains=frozenset({"zephyrdyn.com", "zephyr-careers.net"}),
        aliases=("ZD", "Zephyr Dynamics"),
        account_label="Zephyr",
        entities=(
            EntityRule("Zephyr Labs", ("Zephyr Labs",), True),
            EntityRule("Quillon Instruments", ("Quillon",), False),
            EntityRule("Zephyr (pre-close)", (), False, before="2024-06-01"),
        ),
    )


def northgate() -> WorkspaceProfile:
    return WorkspaceProfile(
        account_name="Northgate Foods Inc",
        account_short="Northgate",
        domain="nfoods.org",
        run_date=date(2026, 3, 1),
        window_from=date(2025, 3, 1),
        window_to=date(2026, 3, 1),
        domains=frozenset({"nfoods.org"}),
        careers_hosts=frozenset({"jobs.northgate-hiring.com"}),
        aliases=("Northgate",),
        account_label="Northgate Foods",
        entities=(EntityRule("Harbor Bakeries", ("harbor bakeries",), False),),
    )


def tag(profile, url="", title="Analyst", text="", first="2026-01-05"):
    return tag_entity(profile, url=url, title=title, text=text, first_seen=first)


class TestProfileRules:
    def test_counted_subsidiary_rule(self):
        assert tag(zephyr(), text="Join Zephyr Labs today") == ("Zephyr Labs", True)

    def test_divested_rule_matches_text_case_insensitively(self):
        assert tag(zephyr(), text="the quillon team") == ("Quillon Instruments", False)

    def test_rule_in_title(self):
        assert tag(zephyr(), title="Quillon Planner") == ("Quillon Instruments", False)

    def test_dated_rule_claims_rows_before_the_date(self):
        assert tag(zephyr(), first="2024-01-01") == ("Zephyr (pre-close)", False)

    def test_dated_rule_does_not_fire_on_or_after_the_date(self):
        assert tag(zephyr(), first="2024-06-01") == ("Zephyr", True)

    def test_dated_rule_never_fires_without_a_first_seen(self):
        assert tag(zephyr(), first="") == ("Zephyr", True)
        assert tag(zephyr(), first=None) == ("Zephyr", True)

    def test_first_seen_may_be_a_date(self):
        assert tag(zephyr(), first=date(2024, 1, 1))[0] == "Zephyr (pre-close)"

    def test_rule_order_decides(self):
        assert tag(zephyr(), text="Zephyr Labs and Quillon")[0] == "Zephyr Labs"

    def test_other_account_has_other_rules(self):
        assert tag(northgate(), text="Harbor Bakeries plant") == ("Harbor Bakeries", False)
        assert tag(northgate(), text="Quillon") == ("Northgate Foods", True)

    def test_bad_regex_is_taken_literally(self):
        prof = WorkspaceProfile(
            account_name="X Co", account_short="X", domain="x.com", run_date=date(2026, 1, 1),
            window_from=date(2025, 1, 1), window_to=date(2026, 1, 1),
            entities=(EntityRule("Odd", ("C++(",), False),),
        )  # fmt: skip
        assert tag(prof, text="we use C++( here")[0] == "Odd"
        assert tag(prof, text="nothing") == ("X", True)


class TestOtherEmployerWording:
    def test_applying_for_a_position_with_other(self):
        text = "By applying for a position with Fenwick Partners you agree to the terms."
        assert tag(zephyr(), text=text) == ("Fenwick Partners", False)

    def test_apply_to_other(self):
        assert tag(northgate(), text="Apply to Brightwater Group. Equal opportunity.") == (
            "Brightwater Group",
            False,
        )

    def test_wording_naming_the_account_is_not_another_employer(self):
        text = "By applying for a position with Zephyr you agree."
        assert tag(zephyr(), text=text) == ("Zephyr", True)

    def test_alias_and_long_name_are_the_account(self):
        assert tag(zephyr(), text="apply to ZD now")[1] is True
        assert tag(zephyr(), text="Apply to Zephyr Dynamics Corporation")[1] is True

    def test_generic_follow_ups_are_not_employers(self):
        for text in (
            "Apply to this role",
            "apply to Jobs online",
            "Apply to Our team",
            "apply to us",
        ):
            assert tag(zephyr(), text=text) == ("Zephyr", True), text

    def test_counted_entity_label_is_not_another_employer(self):
        assert tag(zephyr(), text="applying for a position with Zephyr Labs")[1] is True

    def test_other_employer_beats_account_host(self):
        got = tag(
            zephyr(),
            url="https://zephyrdyn.com/jobs/12345",
            text="applying for a position with Fenwick Partners",
        )
        assert got == ("Fenwick Partners", False)

    def test_lowercase_follow_word_is_not_a_name(self):
        assert tag(zephyr(), text="apply to the team") == ("Zephyr", True)


class TestHostRules:
    def test_account_domain_counts(self):
        assert tag(zephyr(), url="https://careers.zephyrdyn.com/job/1234") == ("Zephyr", True)

    def test_second_account_domain(self):
        assert tag(zephyr(), url="https://zephyr-careers.net/x/55555") == ("Zephyr", True)

    def test_careers_host_of_profile_counts(self):
        assert tag(northgate(), url="https://jobs.northgate-hiring.com/p/88888") == (
            "Northgate Foods",
            True,
        )

    def test_unrelated_host_is_not_the_account(self):
        assert tag(zephyr(), url="https://jobs.stranger.org/p/12345") == (NOT_ACCOUNT, False)

    def test_same_url_is_other_for_other_account(self):
        assert tag(northgate(), url="https://careers.zephyrdyn.com/job/1234")[1] is False

    def test_lookalike_host_is_not_the_account(self):
        assert tag(zephyr(), url="https://zephyrdyn.org/job/1234") == (NOT_ACCOUNT, False)

    def test_job_board_says_nothing(self):
        assert tag(zephyr(), url="https://www.indeed.com/viewjob?jk=abc", text="Zephyr") == (
            "Zephyr",
            True,
        )

    def test_applicant_tracking_host_says_nothing(self):
        got = tag(northgate(), url="https://northgate.wd5.myworkdayjobs.com/en/job/99999")
        assert got == ("Northgate Foods", True)

    def test_blank_host_with_account_text(self):
        assert tag(zephyr(), url="", text="ZD is hiring") == ("Zephyr", True)

    def test_blank_everything_is_the_account(self):
        assert tag(zephyr(), url="", title="", text="") == ("Zephyr", True)

    def test_unparseable_url(self):
        assert tag(zephyr(), url="not a url")[1] is True


class TestHelpers:
    def test_registrables(self):
        assert account_registrables(zephyr()) == {"zephyrdyn.com", "zephyr-careers.net"}
        assert "northgate-hiring.com" in account_registrables(northgate())

    def test_is_account_host(self):
        assert is_account_host(zephyr(), "https://a.b.zephyrdyn.com/x")
        assert not is_account_host(zephyr(), "evil.com")
        assert not is_account_host(zephyr(), "")

    def test_job_board(self):
        assert is_job_board("https://boards.greenhouse.io/x")
        assert not is_job_board("https://zephyrdyn.com")

    @pytest.mark.parametrize(
        ("host", "expected"),
        [
            ("zephyrdyn.org", True),
            ("zephyr-dyn.com", True),
            ("zephyrdyn-careers.com", True),
            ("zephyrdyn.com", False),
            ("careers.zephyrdyn.com", False),
            ("stranger.org", False),
            ("zephyrdynamics-cleaning.com", True),
            ("zephyrdynamicsx.com", False),
            ("indeed.com", False),
            ("", False),
        ],
    )
    def test_domain_collision(self, host, expected):
        assert is_domain_collision(zephyr(), host) is expected

    def test_collision_is_per_profile(self):
        assert is_domain_collision(northgate(), "nfoods.com") is True
        assert is_domain_collision(zephyr(), "nfoods.com") is False

    def test_collision_ignores_short_names(self):
        prof = WorkspaceProfile(
            account_name="AB", account_short="AB", domain="ab.com", run_date=date(2026, 1, 1),
            window_from=date(2025, 1, 1), window_to=date(2026, 1, 1),
        )  # fmt: skip
        assert is_domain_collision(prof, "ab.org") is False

    def test_idempotent(self):
        args = {
            "url": "https://x.org/1",
            "title": "t",
            "text": "apply to Foo Bar",
            "first_seen": "",
        }
        assert tag_entity(zephyr(), **args) == tag_entity(zephyr(), **args)
