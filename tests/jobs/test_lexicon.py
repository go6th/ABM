"""The production lane lexicon follows the owner's research skill (checklist.md scope defaults,
signals.md lane fingerprint questions, phase1-pull.md step 4), 2026-10-24."""

from __future__ import annotations

from jobs.config import load_jobs_config
from jobs.match import match_platforms

CFG = load_jobs_config()
LEXICON = CFG.lexicon


def test_mulesoft_counts_under_salesforce_and_is_a_board_keyword() -> None:
    assert CFG.lane_of("MuleSoft") == "salesforce"
    salesforce = next(lane for lane in CFG.lanes if lane.id == "salesforce")
    assert "MuleSoft" in salesforce.keywords and "Salesforce" in salesforce.keywords


def test_banned_platforms_are_never_terms_or_keywords() -> None:
    terms = {t.casefold() for name, aliases in LEXICON.items() for t in (name, *aliases)}
    keywords = {k.casefold() for lane in CFG.lanes for k in lane.keywords}
    for banned in ("servicemax", "siebel", "databricks"):
        assert banned not in terms and banned not in keywords


def test_satellite_products_match_their_lane() -> None:
    cases = {
        "We run SAP S/4HANA with Ariba and IBP.": ("sap", "SAP"),
        "The Vault PromoMats admin supports medical affairs.": ("veeva", "Veeva"),
        "Build Anypoint integrations between SAP and Salesforce.": ("salesforce", "MuleSoft"),
        "Power BI dashboards sit beside Tableau Server.": ("tableau_analytics", "Power BI"),
        "Snowflake and dbt pipelines on Azure.": ("data_ai", "Snowflake"),
    }
    for text, (lane, platform) in cases.items():
        found = match_platforms("role", text, LEXICON)
        assert platform in found.description, (text, found)
        assert CFG.lane_of(platform) == lane


def test_every_lane_has_a_keyword_and_a_platform() -> None:
    for lane in CFG.lanes:
        assert lane.platforms, lane.id
        assert lane.keywords, lane.id
