"""Person-name validation: synthetic names only, no real person or company."""

from __future__ import annotations

import pytest

from exports.repair import names
from exports.repair.names import is_person_name, parse_person_name


@pytest.mark.parametrize(
    "text",
    [
        "Jane Rivera",
        "Jane Q. Rivera",
        "Jane Q Rivera",
        "J. Rivera",
        "Pieter van der Berg",
        "Maria de la Cruz",
        "Karim bin Salem",
        "Hana al-Faris",
        "José Müller",
        "Łukasz Nowak",
        "Zoë Brontë-Hale",
        "Mary-Jane O'Brien",
        "Siobhan O’Dell",
        "Dr. Jane Rivera",
        "Tomas Varga Jr.",
        "Tomas Varga III",
        "Will Okoro",
        "Said Farouk",
        "Md Rahim",
        "王 小明",
        "Ли Ван",
        "Anne-Marie Dupont",
        "Priya Raman Iyer Nair Kumar",
        "  Jane   Rivera  ",
    ],
)
def test_valid_names(text: str) -> None:
    assert is_person_name(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "Jane",
        "Unknown",
        "[Name]",
        "N/A",
        "n/a",
        "Unknown Person",
        "Test User",
        "Acme Corp",
        "Quillon Holdings",
        "Rivera Partners",
        "Jane Rivera, CEO",
        "Jane Rivera (she/her)",
        'Jane "JR" Rivera',
        "Jane 2 Rivera",
        "Jane R1vera",
        "jane rivera",
        "JANE RIVERA",
        "Jane Rivera Chief Executive Officer",
        "Chief Executive Officer",
        "Vice President Sales",
        "Technology Product Management",
        "The Board",
        "Head of Data",
        "Serves as chairman",
        "Jane Q.",
        "Dr. Rivera",
        "van der Berg",
        "Jane van",
        "J Rivera",
        "a b c d e f",
        "Jane Rivera: CEO",
        "Jane Rivera / Acme",
        "Jane Rivera@Acme",
        "Acme.com Team",
        "SAP Team",
        "Jane Rivera.",
        "I Think Markets",
        "A" * 90 + " Rivera",
    ],
)
def test_invalid_names(text: str) -> None:
    assert not is_person_name(text)


def test_non_string_is_not_a_name() -> None:
    assert not is_person_name(None)  # type: ignore[arg-type]
    assert parse_person_name(None) is None  # type: ignore[arg-type]


def test_token_count_limits() -> None:
    assert not is_person_name("Rivera")
    assert is_person_name("Ana Lopez")
    assert is_person_name("Ana Maria Isabel Lopez Rey")
    assert not is_person_name("Ana Maria Isabel Lopez Rey Soto")


def test_stopword_list_is_generic_data() -> None:
    # a module level tuple of lower-case words with the verbs and title words the brief names
    assert isinstance(names.STOPWORDS, tuple)
    for word in ("serves", "joined", "named", "appointed", "chairman", "chief", "president",
                 "officer", "director", "vice", "head", "manager", "executive", "worldwide",
                 "global", "of", "the", "and", "at", "for", "with", "as", "is", "was", "who",
                 "will", "has", "have", "said", "announced"):  # fmt: skip
        assert word in names.STOPWORDS
    assert all(w == w.lower() for w in names.STOPWORDS)


def test_stopwords_name_no_account() -> None:
    # nothing in the lists is a company, brand or person
    banned = {"acme", "zephyr", "lumora", "salesforce", "sap", "workday", "microsoft"}
    assert not banned & set(names.STOPWORDS)
    assert not banned & set(names.PARTICLES)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Jane Q. Rivera, chairman, CEO and president of Acme",
            ("Jane Q. Rivera", "chairman, CEO and president of Acme"),
        ),
        (
            "Bilal Muhsin serves as executive vice president and president of the Care unit",
            ("Bilal Muhsin", "executive vice president and president of the Care unit"),
        ),
        ("Tomas Varga - Head of Data and Analytics", ("Tomas Varga", "Head of Data and Analytics")),
        ("Tomas Varga – Head of Data", ("Tomas Varga", "Head of Data")),
        ("Tomas Varga | VP Operations", ("Tomas Varga", "VP Operations")),
        ("Tomas Varga: chief digital officer", ("Tomas Varga", "chief digital officer")),
        ("Tomas Varga (CEO of Lumora)", ("Tomas Varga", "CEO of Lumora")),
        ("Pieter van der Berg, senior director", ("Pieter van der Berg", "senior director")),
        (
            "Hana Faris joined Lumora as chief digital officer",
            ("Hana Faris", "chief digital officer"),
        ),
        ("Hana Faris was named chief financial officer", ("Hana Faris", "chief financial officer")),
        (
            "Hana Faris has been appointed as the new managing director",
            ("Hana Faris", "managing director"),
        ),
        ("Hana Faris is the head of procurement", ("Hana Faris", "head of procurement")),
        ("Will Okoro, director of sales", ("Will Okoro", "director of sales")),
    ],
)
def test_parse_positive(text: str, expected: tuple[str, str]) -> None:
    assert parse_person_name(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Jane Rivera",
        "worldwide president of the Care business",
        "President of the Care business",
        "I think the market will shift",
        "The company said revenue grew",
        "Jane Rivera, PhD",
        "Jane Rivera, Acme",
        "Jane Rivera said the market is up",
        "Jane Rivera is a good person",
        "Jane Rivera joined Lumora last year",
        "Jane Rivera Chief Executive Officer",
        "Jane Rivera of Acme, CEO",
        "Jane, chief executive",
        "Chief Executive Officer, Jane Rivera",
        "Acme Corp, chief executive",
        "Jane Rivera - ",
        '"We are growing," said Jane Rivera, CEO',
        "serves as chief executive",
        "A B C D E, chief",
    ],
)
def test_parse_negative(text: str) -> None:
    assert parse_person_name(text) is None


def test_parse_result_name_is_always_valid() -> None:
    for text in (
        "Jane Q. Rivera, chairman and CEO",
        "Tomas Varga - Head of Data",
        "Hana Faris was named chief financial officer",
    ):
        parsed = parse_person_name(text)
        assert parsed is not None
        assert is_person_name(parsed[0])
        assert parsed[1]


def test_parse_is_deterministic() -> None:
    text = "Jane Q. Rivera, chairman, CEO and president of Acme"
    assert parse_person_name(text) == parse_person_name(text)


# ---- structured names: a provider's own name field -------------------------------------------
from exports.repair.names import is_profile_name, normalise_profile_name  # noqa: E402


@pytest.mark.parametrize(
    ("raw", "plain"),
    [
        ("Dana (Dee) Quill", "Dana Quill"),
        ("Dana Q. Lee, MBA, PMP", "Dana Q. Lee"),
        ("Rowan Marsh MBA, MS", "Rowan Marsh"),
        ("Ines Alvarez - Prieto", "Ines Alvarez-Prieto"),
        ("Tomas Berg, CPA", "Tomas Berg"),
        ("Plain Name", "Plain Name"),
    ],
)
def test_normalise_profile_name(raw: str, plain: str) -> None:
    assert normalise_profile_name(raw) == plain


@pytest.mark.parametrize(
    "name",
    ["Dana Quill", "Mia He", "Sarah B.", "Víctor A.", "Joe C.", "Jean de la Cruz", "Ana-Lu Pérez"],
)
def test_profile_names_accepted(name: str) -> None:
    assert is_profile_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "Madonna",
        "[Name]",
        "Chief Executive Officer",
        "A. B.",
        "VP Sales",
        "Mia 2nd",
        "worldwide president of X",
        "Pat Lee, the CEO",
    ],
)
def test_profile_names_rejected(name: str) -> None:
    assert not is_profile_name(name)
