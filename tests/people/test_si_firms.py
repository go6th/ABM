"""Former SI and consulting employers (the People List column): the 28 firms of the owner's
reference list matched by employer name, dated stints printed ``Firm (2015 to 2019)``, a stint
still running ``Firm (2020 to date)``, nothing printed for a person with no profile read. Employer
names here are the firms' own public names; every person is synthetic."""

from __future__ import annotations

import pytest

from people.config import PeopleConfig
from people.models import Employment, PartialDate, Profile, RawProfile
from people.profiles import to_profile
from people.si_firms import firm_of, former_si_employers
from tests.people.conftest import FROZEN  # noqa: F401  (the fixture config is the frozen copy)


def _profile(**kw: object) -> Profile:
    base: dict[str, object] = {
        "person_key": "pk",
        "profile_url": "https://www.linkedin.com/in/synthetic",
        "provider_code": "p_people",
        "raw_ref": "raw",
        "call_ref": "call",
    }
    base.update(kw)
    return Profile(**base)  # type: ignore[arg-type]


def _stint(employer: str, start: str | None, end: str | None) -> Employment:
    return Employment(employer=employer, start=PartialDate.parse(start), end=PartialDate.parse(end))


@pytest.mark.parametrize(
    ("employer", "firm"),
    [
        ("Accenture", "Accenture"),
        ("Accenture Federal Services", "Accenture"),
        ("Deloitte Consulting LLP", "Deloitte"),
        ("PricewaterhouseCoopers", "PwC"),
        ("PwC", "PwC"),
        ("EY", "EY"),
        ("Ernst & Young LLP", "EY"),
        ("KPMG US", "KPMG"),
        ("Capgemini Invent", "Capgemini"),
        ("Cognizant Technology Solutions", "Cognizant"),
        ("Infosys Limited", "Infosys"),
        ("Tata Consultancy Services", "TCS"),
        ("TCS", "TCS"),
        ("Wipro Technologies", "Wipro"),
        ("HCL Technologies", "HCL"),
        ("IBM Global Business Services", "IBM"),
        ("McKinsey & Company", "McKinsey"),
        ("Boston Consulting Group", "BCG"),
        ("Bain & Company", "Bain"),
        ("Tech Mahindra", "Tech Mahindra"),
        ("LTIMindtree", "LTIMindtree"),
        ("Mindtree", "LTIMindtree"),
        ("Genpact", "Genpact"),
        ("DXC Technology", "DXC"),
        ("Slalom Consulting", "Slalom"),
        ("Persistent Systems", "Persistent Systems"),
        ("CitiusTech", "CitiusTech"),
        ("Virtusa", "Virtusa"),
        ("Hexaware Technologies", "Hexaware"),
        ("NTT DATA", "NTT Data"),
        ("Mphasis", "Mphasis"),
        ("EPAM Systems", "EPAM"),
        ("Globant", "Globant"),
    ],
)
def test_each_firm_is_found_by_its_names(cfg: PeopleConfig, employer: str, firm: str) -> None:
    assert firm_of(employer, cfg.si_firms) == firm


@pytest.mark.parametrize(
    "employer",
    ["Synthetic Pharma Co", "Keyboard Makers", "The Accentuate Group", "Beyond Meat", "Eyewear Co"],
)
def test_other_employers_are_not_firms(cfg: PeopleConfig, employer: str) -> None:
    assert firm_of(employer, cfg.si_firms) is None


def test_the_list_has_28_distinct_firms(cfg: PeopleConfig) -> None:
    assert len(cfg.si_firms) == 28


def test_dated_stints_print_firm_years_and_to_date(cfg: PeopleConfig) -> None:
    profile = _profile(
        employments=(
            _stint("Accenture", "2020", None),
            _stint("Synthetic Pharma Co", "2019", "2020"),
            _stint("Deloitte Consulting", "2015-03", "2019-01"),
            _stint("PwC", None, "2014"),
        )
    )
    assert former_si_employers(profile, cfg.si_firms) == (
        "Accenture (2020 to date)",
        "Deloitte (2015 to 2019)",
        "PwC",
    )
    # the cell is the entries joined by "; " in the workbook
    assert "; ".join(former_si_employers(profile, cfg.si_firms) or ()) == (
        "Accenture (2020 to date); Deloitte (2015 to 2019); PwC"
    )


def test_a_repeated_identical_stint_prints_once_and_two_stints_print_twice(
    cfg: PeopleConfig,
) -> None:
    profile = _profile(
        employments=(
            _stint("EY", "2001", "2003"),
            _stint("EY", "2001", "2003"),
            _stint("EY", "2008", "2010"),
        )
    )
    assert former_si_employers(profile, cfg.si_firms) == ("EY (2001 to 2003)", "EY (2008 to 2010)")


def test_undated_prior_employers_print_the_firm_alone(cfg: PeopleConfig) -> None:
    profile = _profile(prior_employers=("Infosys Ltd", "Synthetic Pharma Co", "Infosys"))
    assert former_si_employers(profile, cfg.si_firms) == ("Infosys",)


def test_no_profile_is_none_and_a_profile_with_no_firm_is_empty(cfg: PeopleConfig) -> None:
    assert former_si_employers(None, cfg.si_firms) is None
    assert former_si_employers(_profile(), cfg.si_firms) is None  # the source gave no experience
    assert former_si_employers(_profile(employments=()), cfg.si_firms) == ()
    assert (
        former_si_employers(
            _profile(employments=(_stint("Synthetic Pharma Co", "2019", None),)), cfg.si_firms
        )
        == ()
    )


def _raw(fields: dict[str, object]) -> RawProfile:
    return RawProfile(
        profile_url="https://www.linkedin.com/in/synthetic",
        provider_code="p_people",
        raw_ref="raw",
        call_ref="call",
        fields=fields,
    )


def test_employments_are_parsed_typed_and_bad_entries_mark_the_profile_partial(
    cfg: PeopleConfig,
) -> None:
    good = to_profile(
        _raw(
            {
                "employments": [
                    {"employer": "Accenture", "start": 2015, "end": "2019"},
                    {"employer": "Deloitte", "start": "2020-02"},
                ]
            }
        ),
        "pk",
        cfg,
        is_hostile=lambda _t: False,
    )
    assert good.employments is not None and not good.partial
    assert [
        (e.employer, e.start.year if e.start else None, e.end.year if e.end else None)
        for e in good.employments
    ] == [
        ("Accenture", 2015, 2019),
        ("Deloitte", 2020, None),
    ]
    bad = to_profile(
        _raw(
            {
                "employments": [
                    {"employer": "Accenture", "start": "not a date"},
                    {"start": "2015"},
                    "Deloitte",
                    {"employer": "KPMG", "start": "2012", "end": "2014"},
                ]
            }
        ),
        "pk",
        cfg,
        is_hostile=lambda _t: False,
    )
    assert bad.partial and bad.employments is not None
    assert [e.employer for e in bad.employments] == ["KPMG"]
    wrong = to_profile(
        _raw({"employments": "Accenture 2015 to 2019"}), "pk", cfg, is_hostile=lambda _t: False
    )
    assert wrong.employments is None and wrong.partial
    absent = to_profile(_raw({"name": "Synthetic"}), "pk", cfg, is_hostile=lambda _t: False)
    assert absent.employments is None and not absent.partial
