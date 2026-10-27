"""The entity lock (master prompt v2 section 3): the home page must name the account, look-alike
brands are blocked, and every record passes ``accept`` by host or by naming the account.
GUARANTEE(test_a_lookalike_domain_is_refused_before_any_pull)
"""

from __future__ import annotations

from agents.entity import (
    EntityLock,
    accept,
    brand_token,
    build_lock,
    edit_distance,
    find_lookalikes,
    host_of,
    registrable,
)

STOP = ("inc", "corporation", "the", "company", "ltd", "llc", "group", "health")


def test_hosts_and_brands_fold_the_way_the_rule_needs() -> None:
    assert host_of("https://www.Careers.VizientInc.com/jobs?x=1") == "careers.vizientinc.com"
    assert registrable("careers.vizientinc.com") == "vizientinc.com"
    assert registrable("jobs.acme.co.uk") == "acme.co.uk"
    assert brand_token("Vizient, Inc.", STOP) == "vizient"
    assert brand_token("The Kraft Heinz Company", STOP) == "kraft"
    assert brand_token("Inc.", STOP) == "inc"
    assert edit_distance("vizent", "vizient") == 1 and edit_distance("abc", "abc") == 0


def test_a_lookalike_domain_is_refused_before_any_pull() -> None:
    """The 2026-10-24 case: an account named Vizient researched on vizent.co, a 3D studio."""
    lock = build_lock(
        legal_name="Vizient, Inc.",
        primary_domain="vizent.co",
        stopwords=STOP,
        home_url="https://vizent.co/",
        final_url="https://vizent.co/",
        home_title="Vizent Solutions, 3D modeling studio",
        home_text=(
            "We build 3D models and renders for architecture, product design and game studios."
        ),
    )
    assert lock.confirmed is False and lock.mismatch is True
    assert lock.problems == ("home page of vizent.co does not name Vizient, Inc.",)


def test_a_site_that_never_says_the_legal_name_is_unconfirmed_not_a_mismatch() -> None:
    """gsk.com for GlaxoSmithKline: the brand is not in the domain or the page, and the domain is
    nowhere near the brand, so the run goes on unverified (the lock stops look-alikes only)."""
    page = (
        "GSK is a global biopharma company with a purpose to unite science, technology and talent."
    )
    lock = build_lock(
        legal_name="GlaxoSmithKline plc",
        primary_domain="gsk.com",
        stopwords=STOP,
        home_url="https://gsk.com/",
        final_url=None,
        home_title="GSK",
        home_text=page,
    )
    assert lock.confirmed is False and lock.mismatch is False


def test_a_domain_that_carries_the_brand_is_confirmed_even_when_the_page_is_blocked() -> None:
    lock = build_lock(
        legal_name="Vizient, Inc.",
        primary_domain="vizientinc.com",
        stopwords=STOP,
        home_url="https://vizientinc.com/",
        final_url=None,
        home_title="Just a moment...",
        home_text="Enable JavaScript and cookies to continue",
    )
    assert lock.confirmed is True and lock.mismatch is False


def test_the_lock_records_redirects_subsidiaries_boards_and_lookalikes() -> None:
    hits = [
        ("https://vizientinc.com/about", "Vizient, Inc."),
        ("https://vizent.co/", "Vizent Solutions 3D studio"),
        ("https://vizientmfg.com/", "Vizient Manufacturing Solutions, Bettendorf"),
        ("https://vizient.de/", "Vizient Germany"),
        ("https://news.example.com/vizient", "Vizient names new CTO"),
    ]
    lock = build_lock(
        legal_name="Vizient, Inc.",
        primary_domain="vizientinc.com",
        stopwords=STOP,
        home_url="https://vizientinc.com/",
        final_url="https://www.vizient.com/",
        home_title="Vizient | Healthcare performance improvement",
        home_text="Vizient, Inc. is the largest member-driven health care performance company.",
        subsidiaries=(("Kaufman Hall", "kaufmanhall.com"), ("Sg2", "")),
        careers_hosts=("careers.vizientinc.com",),
        search_hits=hits,
    )
    assert lock.confirmed and lock.problems == ()
    assert lock.allowed_domains == {"vizientinc.com", "vizient.com", "kaufmanhall.com"}
    assert lock.redirected_to == "vizient.com"
    assert [x.host for x in lock.blocked_lookalikes] == ["vizent.co"]
    # the brand on another registry (vizient.de) and a longer different name are not look-alikes
    assert "vizientmfg.com" not in {x.host for x in lock.blocked_lookalikes}
    rebuilt = EntityLock.from_dict(lock.as_dict())
    assert rebuilt == lock


def test_accept_keeps_own_hosts_and_third_parties_that_name_the_account() -> None:
    lock = build_lock(
        legal_name="Vizient, Inc.",
        primary_domain="vizientinc.com",
        stopwords=STOP,
        home_url="https://vizientinc.com/",
        final_url=None,
        home_title="Vizient",
        home_text="",
        careers_hosts=("careers.vizientinc.com",),
        search_hits=[("https://vizent.co/", "Vizent Solutions")],
    )
    assert accept(lock, "https://www.vizientinc.com/newsroom/x")[0]
    assert accept(lock, "https://careers.vizientinc.com/job/1")[0]
    assert accept(lock, "https://vizent.co/portfolio") == (False, "lookalike host vizent.co")
    assert accept(lock, "https://www.beckershospitalreview.com/a", "Vizient names a new CTO")[0]
    assert accept(lock, "https://www.beckershospitalreview.com/a", "A hospital bought beds") == (
        False,
        "no account name in text",
    )
    kept, _why = accept(lock, "https://blog.example.com/3d", "Vizent studio ships a new render")
    assert kept is False
    assert accept(lock, None, None) == (False, "no url")
    assert find_lookalikes("vizient", [("vizient.com", "x")], ["vizientinc.com"]) == ()


def test_a_posting_on_a_lookalike_host_is_dropped_before_it_is_stored() -> None:
    from jobs.adapters import ConnectorPostingSource

    source = ConnectorPostingSource(None, "careers.vizientinc.com", ("vizent.co",))
    assert source._lookalike("https://www.vizent.co/careers/3d-artist") is True
    assert source._lookalike("https://careers.vizientinc.com/job/1") is False
    assert source._lookalike(None) is False
    assert source.dropped == 1


def test_the_careers_check_accepts_the_locks_other_domains() -> None:
    from datetime import UTC, datetime

    import pytest

    from datamodel.careers import ConstructedUrl, Visit, check

    visit = Visit("https://careers.vizient.com/jobs", "fetch-1", datetime.now(UTC))
    with pytest.raises(ConstructedUrl):
        check(visit, "vizientinc.com")
    assert check(visit, "vizientinc.com", ("vizient.com",)) == visit.url
