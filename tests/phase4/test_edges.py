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
