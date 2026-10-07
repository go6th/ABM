"""Edge cases of the Phase 4 rule modules. Lying scenarios: an item both expired and present, every
change kind in the Summary diff, unconfigured channels and CRM adapters that must refuse."""

from __future__ import annotations

from datetime import date

import pytest

from account_intelligence.platform.ports import NotConfigured
from alerts.channels import UnconfiguredSlack, UnconfiguredWebhook
from crm_writeback import client, hubspot, salesforce
from refresh import delta
from refresh.summary_diff import render
from tests.phase4.conftest import NOW


def fv(ident: str, grade: str) -> delta.FactView:
    return delta.FactView(f"f-{ident}", ident, "news_event", grade, "S-0001")


def test_an_item_both_expired_and_present_is_not_reported_expired() -> None:
    d = delta.compute(
        [fv("a", "probable")],
        [fv("a", "probable")],
        [fv("a", "probable")],
        {},
        {},
        checked=[],
        checked_at=NOW,
    )
    assert d.no_change


def test_the_diff_renders_every_change_kind() -> None:
    d = delta.compute(
        [fv("old", "confirmed"), fv("up", "lead")],
        [fv("up", "probable"), fv("fresh", "hypothesis")],
        [fv("old", "confirmed")],
        {"open:sap": delta.Count("open:sap", 1, "postings in March")},
        {"open:sap": delta.Count("open:sap", 4, "postings in April")},
        checked=["company_news"],
        checked_at=NOW,
    )
    text = render(d, since_version=2, since_date=date(2026, 3, 1))
    assert "- fresh (hypothesis) [S-0001]" in text
    assert "- up: lead to probable [S-0001]" in text
    assert "Expired (moved to history)\n- old (was confirmed) [S-0001]" in text
    assert "- open:sap: 1 to 4 (base: postings in April)" in text
    ungraded = delta.compute(
        [],
        [delta.FactView("f", "x", "news_event", None, None)],
        [],
        {},
        {},
        checked=[],
        checked_at=NOW,
    )
    assert "- x (ungraded)" in render(ungraded, since_version=1, since_date=date(2026, 3, 1))


@pytest.mark.asyncio
async def test_unconfigured_channels_and_crm_adapters_refuse() -> None:
    with pytest.raises(NotConfigured):
        await UnconfiguredSlack().post("u", "t", idempotency_key="k")
    with pytest.raises(NotConfigured):
        await UnconfiguredWebhook().post(b"{}", {})
    draft = client.Draft("note", "001A", "t", "b", "m")
    for crm in (salesforce.SalesforceCrm(), hubspot.HubSpotCrm()):
        with pytest.raises(NotConfigured):
            await crm.create(draft)
        with pytest.raises(NotConfigured):
            await crm.find_by_marker("m")


def test_score_components_defaults_and_recency_boundaries() -> None:
    from dataclasses import replace
    from datetime import timedelta

    from signals import score

    x = score.Inputs("news_event", "confirmed", "t1", date(2026, 3, 1), False, False)
    s = score.score(x, date(2026, 3, 1))
    assert [k for k, _ in s.components] == [
        "signal_type",
        "grade",
        "source_tier",
        "recency",
        "lane_match",
        "priority_a",
    ]
    assert (
        dict(score.score(replace(x, grade="internal"), date(2026, 3, 1)).components)["grade"] == 0
    )
    assert (
        dict(score.score(replace(x, source_tier="t9"), date(2026, 3, 1)).components)["source_tier"]
        == 0
    )
    rec = {
        d: dict(
            score.score(
                replace(x, observed_on=date(2026, 3, 1) - timedelta(days=d)), date(2026, 3, 1)
            ).components
        )["recency"]
        for d in (0, 7, 8, 30, 31, 90, 91, -1)
    }
    assert rec == {0: 15, 7: 15, 8: 8, 30: 8, 31: 3, 90: 3, 91: 0, -1: 0}


def test_policy_accepts_status_provenance_equal_bounds_and_one_day_periods() -> None:
    from refresh import policy

    p = policy.parse(
        {
            "version": "x",
            "cadence_days": {"weekly": 1, "monthly": 1, "quarterly": 1},
            "tenant_override_bounds": {"min_factor": 1, "max_factor": 1},
            "signals": {"a": {"cadence": "weekly", "status": "proposal"}},
        }
    )
    assert p.entry("a").provenance == "proposal" and p.entry("a").days == 1
    assert policy.effective_days(p.entry("a"), p, 1) == 1
    with pytest.raises(policy.PolicyInvalid):
        policy.parse(
            {
                "version": "x",
                "cadence_days": {"weekly": 0, "monthly": 1, "quarterly": 1},
                "tenant_override_bounds": {"min_factor": 1, "max_factor": 1},
                "signals": {},
            }
        )


def test_fingerprints_are_pinned_to_their_canonical_form() -> None:
    import hashlib
    import json

    from signals import fingerprint

    got = fingerprint.fingerprint(
        "news_event", "Acme Inc.", {"Title": "Big News!"}, date(2026, 3, 2), bucket_days=7
    )
    canon = {
        "type": "news_event",
        "entity": "acme",
        "attrs": {"Title": "big news"},
        "bucket": date(2026, 3, 2).toordinal() // 7,
    }
    assert got == hashlib.sha256(json.dumps(canon, sort_keys=True).encode()).hexdigest()
    assert fingerprint.date_bucket(date(2026, 3, 2), 1) == date(2026, 3, 2).toordinal()


def test_the_create_only_guard_refuses_any_other_public_method() -> None:
    class Export:
        kind = "salesforce"

        async def create(self, d: client.Draft) -> client.Created: ...
        async def export_all(self) -> None: ...  # not forbidden by name, still not allowed

    with pytest.raises(client.NotCreateOnly) as e:
        client.assert_create_only(Export)
    assert "export_all" in str(e.value)

    class Good:
        kind = "hubspot"

        async def create(self, d: client.Draft) -> client.Created: ...
        async def find_account(self, domain: str) -> list[str]: ...

    assert client.assert_create_only(Good) is Good


def test_sensitive_subject_and_unknown_source_text_are_exact() -> None:
    from alerts import sensitive

    assert sensitive.subject("breach") == "Account context update"
    assert sensitive.render("lawsuit", None) == (
        "Preparation only, not an opening line. A legal proceeding was reported (source on file). "
        "Review it before any contact so you are aware of the context; do not raise it."
    )
