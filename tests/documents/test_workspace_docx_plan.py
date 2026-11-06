"""The Word import plan (``documents.workspace_docx.plan``), no database, on the rows a real
Account Summary carried (UCB, 2026-11-04) that rolled the whole workspace import back: a Source
links row whose Domain is a label ("6th agents data pull", "Internal") has no web address, so it
is counted and left out instead of reaching the source register as ``https://6th agents data
pull/``; a lane-meaning paragraph led by "Salesforce (interpretation)." keeps the lane id
``salesforce`` (``ck_report_sections_lane``), not the words. SYNTHETIC text only."""

from __future__ import annotations

from types import SimpleNamespace

from documents.workspace_docx import plan

MEMBER = "ws/deliverables/Acme_Account_Summary.docx"


def _row(table: str, **cells: str) -> SimpleNamespace:
    content = "; ".join(f"{k}: {v}" for k, v in cells.items())
    return SimpleNamespace(content=content, location={"member": MEMBER, "table": table})


def test_a_label_in_the_domain_cell_is_not_a_web_address() -> None:
    """See the module docstring."""
    rows = [
        _row("t10", ID="S1", Source="Acme leadership page", Domain="acme.example"),
        _row("t10", ID="S2", Source="Job feed export", Domain="6th agents data pull"),
        _row("t10", ID="S3", Source="Owner notes", Domain="Internal"),
        _row("t10", ID="S4", Source="Careers (https://careers.acme.example/jobs)", Domain="x y"),
    ]
    out = plan(rows, [], [])
    assert [(s["alias"], s["url"]) for s in out.sources] == [
        ("S1", "https://acme.example/"),
        ("S4", "https://careers.acme.example/jobs"),
    ]
    assert out.counts["docx_sources_no_link"] == 2


def test_a_lane_meaning_keeps_the_lane_id() -> None:
    """See the module docstring."""
    section = SimpleNamespace(
        heading_path="1. The account in plain words > What it likely means for each lane",
        content=(
            "Salesforce (interpretation). The best entry point is the patient services estate.\n"
            "Data and AI (interpretation). The lab automation work needs data engineering.\n"
            "Something else entirely without a lane, which is still kept as a paragraph."
        ),
    )
    out = plan([], [section], [])
    assert [(s["kind"], s["lane"]) for s in out.sections] == [
        ("lane_meaning", "salesforce"),
        ("lane_meaning", "data_ai"),
        ("lane_meaning", None),
    ]


def test_ucb_style_signal_platform_and_profile_tables_import() -> None:
    """The UCB Summary heads its signal tables "Signal", its platform picture "Lane / What we
    see" and its profile findings "Finding / What the profiles show"; each still imports."""
    rows = [
        _row("Tier 1", Signal="New CRM lead hired", Grade="Confirmed", Lanes="Salesforce",
             Date="Oct 2026", Source="S1, J2"),
        _row("p", Lane="SAP", **{"What we see": "S/4HANA planning in Bulle"}, Grade="Probable",
             Reasoning="Two postings", Source="S4"),
        _row("f", Finding="Veeva alumni", Grade="Lead",
             **{"What the profiles show": "Five ex-Veeva staff"}, Interpretation="CRM depth"),
    ]  # fmt: skip
    out = plan(rows, [], [])
    by_key = {f["attribute_key"].split(".")[0]: f for f in out.facts}
    signal = by_key["signals"]
    assert signal["value"] == "New CRM lead hired" and signal["grade"] == "confirmed"
    # J codes are job references, not sources
    assert signal["lane"] == "salesforce" and signal["cites"] == ["S1"]
    assert by_key["platform"]["value"] == "SAP: S/4HANA planning in Bulle"
    assert by_key["platform"]["cites"] == ["S4"]
    assert by_key["profiles"]["value"] == "Veeva alumni: Five ex-Veeva staff"
    assert out.counts["docx_tables_unknown"] == 0
