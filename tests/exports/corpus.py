"""Synthetic export corpus (spec 9.1). Every account, person, URL and sentence is synthetic and
labelled so; domains use the reserved ``.example`` top-level domain. No real tenant data.

``account(...)`` builds a valid ``SnapshotContent`` whose build passes every rule; the variants
(rich, sparse, hostile, partner, outage, conflicting, two tenants on one domain) and the lying
tests derive from it with ``model_copy``. The tracker rows and tally come from the tracker's own
client view (``render_client_view``), never typed here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from account_intelligence.tracker.config import load_checklist
from account_intelligence.tracker.gap import TrackerRowState
from account_intelligence.tracker.model import ReasonCode, Status
from account_intelligence.tracker.render import render_client_view
from account_intelligence.tracker.tally import sort_for_rendering
from exports.content import (
    DetectionRow,
    EvidenceRow,
    FactRow,
    FindingRow,
    JobsCounts,
    PeopleCounts,
    PersonRow,
    PostingRow,
    SectionRow,
    SnapshotContent,
    SourceRow,
    TrackerLine,
)

ROUTING = Path(__file__).resolve().parents[2] / (
    ".claude/skills/account-intelligence-platform/assets/routing.yaml"
)


def broker(code: str) -> str:
    """A data broker's internal display name, read from the routing asset so no test spells a
    vendor name (they live only in allowed places)."""
    return str(yaml.safe_load(ROUTING.read_text())["providers"][code]["name"])


AS_OF = date(2026, 9, 30)
TENANT = "00000000-0000-4000-8000-0000000000a1"
TENANT_B = "00000000-0000-4000-8000-0000000000b2"
UNICODE = "Synthetic شركة חברה \U0001f680 café"
CONTROL = "Synthetic\x0b value\x00 with\x1f controls"
FORMULAS = ('=HYPERLINK("https://evil.example")', "@SUM(A1:A9)", "+1+1", "-2-2")


def tracker(
    statuses: dict[str, Status | None] | None = None,
) -> tuple[tuple[TrackerLine, ...], tuple[str, ...]]:
    """Tracker lines and tally from the tracker's client view over the 33 checklist rows."""
    rows = []
    for i, r in enumerate(load_checklist().rows):
        status = (statuses or {}).get(r.key, Status.DONE if i % 4 else Status.NONE)
        reason = ReasonCode.SOURCE_UNAVAILABLE if status == Status.BLOCKED else None
        rows.append(
            TrackerRowState(
                key=r.key,
                priority=r.priority,
                status=status,
                reason_code=reason,
                proof_hash=None,
                swept=False,
            )
        )
    view = render_client_view(rows, {})
    ordered = list(sort_for_rendering(rows))
    lines = tuple(
        TrackerLine(
            key=r.key,
            row=v["row"],
            status=v["status"],
            priority=v["priority"],
            reason=v["reason"],
            tools=tuple(v["tools"]),
            results=v["results"],
        )
        for r, v in zip(ordered, view["rows"], strict=True)
    )
    return lines, tuple(view["tally"])


@dataclass
class Sources:
    """Allocates registry codes for URLs in order (as the source registry would)."""

    host: str
    rows: list[SourceRow] = field(default_factory=list)

    def add(self, path: str, kind: str = "primary", published: date | None = AS_OF) -> SourceRow:
        """Register a URL and return its row."""
        row = SourceRow(
            code=f"S-{len(self.rows) + 1:04d}",
            url=f"https://{self.host}/{path}",
            published=published,
            kind=kind,
        )
        self.rows.append(row)
        return row


def account(
    name: str = "Synthetic Alpha Ltd",
    host: str = "alpha.example",
    *,
    tenant: str = TENANT,
    run: str = "00000000-0000-4000-8000-00000000a001",
    account_id: str = "00000000-0000-4000-8000-00000000c001",
    n_facts: int = 6,
    n_people: int = 4,
    n_postings: int = 3,
    n_detections: int = 2,
    long_text: bool = False,
    hostile_text: bool = False,
    partner: bool = False,
) -> SnapshotContent:
    """A valid synthetic account (see the module docstring)."""
    src = Sources(host)
    lanes = ("salesforce", "servicenow", "sap", None)
    grades = ("confirmed", "probable", "hypothesis", "lead")
    facts: list[FactRow] = []
    evidence: list[EvidenceRow] = []
    for i in range(n_facts):
        s = src.add(f"news/item-{i}", kind="primary" if i % 4 == 0 else "secondary")
        grade = grades[i % 4]
        value = f"Synthetic finding {i} for {name}"
        if hostile_text and i == 1:
            value = f"{UNICODE} {CONTROL} {FORMULAS[0]}"
        fid = f"00000000-0000-4000-8000-{i:012d}"
        facts.append(
            FactRow(
                id=fid,
                attribute_key=f"tech.synthetic_{i}",
                value_text=value,
                lane=lanes[i % 4],
                claim_kind="sourced",
                product_grade=grade,
                engine_grade="A" if i % 4 == 0 else "B",
                read_in_primary_this_run=grade == "confirmed",
                may_state_to_prospect=grade == "confirmed",
                confirming_question=None if grade == "confirmed" else f"Synthetic question {i}?",
                flags=(),
                lead_status="complete" if grade == "lead" else None,
            )
        )
        evidence.append(
            EvidenceRow(
                id=f"10000000-0000-4000-8000-{i:012d}",
                fact_id=fid,
                evidence_kind="source",
                url=s.url,
                supporting_sentence=f"Synthetic sentence {i} relied on.",
                source_code=s.code,
                is_aggregator=False,
                origin="full_page_read" if i % 4 == 0 else "search_snippet",
                evidence_date=AS_OF,
            )
        )
    people = []
    names: list[tuple[str, bool]] = []
    for i in range(n_people):
        s = src.add(f"people/person-{i}", kind="secondary")
        pname = f"Synthetic Person {i:03d}"
        if hostile_text and i == 0:
            pname = f"{FORMULAS[1]} {UNICODE}"
        people.append(
            PersonRow(
                id=f"pk-{i:04d}",
                name=pname,
                title=f"Synthetic Director {i}",
                level="director",
                lanes="salesforce",
                priority="A" if i < 2 else "B",
                profile_url=s.url,
                source_code=s.code,
            )
        )
        names.append((pname, True))
    names += [("Synthetic Excluded Person", False), ("Synthetic Merged Person", False)]
    findings = []
    if people:
        s = src.add("posts/post-0", kind="secondary")
        findings.append(
            FindingRow(
                person_id=people[0].id,
                claim="Synthetic claim from a post",
                sentence="Synthetic own words from the post.",
                url=s.url,
            )
        )
    postings = []
    for i in range(n_postings):
        s = src.add(f"jobs/posting-{i}", kind="primary")
        description = f"Synthetic description {i}. Works with Salesforce daily."
        if long_text and i == 0:
            description = ("Synthetic long text. " * 2200) + "Salesforce end."
        if hostile_text and i == 1:
            description = f"{FORMULAS[0]} {CONTROL} {UNICODE}"
        postings.append(
            PostingRow(
                posting_key=f"post-{i:05d}",
                title=f"Synthetic Salesforce Admin {i}",
                url=s.url,
                status=("open", "closed", "unknown")[i % 3],
                posting_class="admin_owner",
                platforms=("Salesforce",),
                platform_sentences=(f"Synthetic sentence naming Salesforce {i}.",),
                description=description,
                posted_at=date(2026, 1 + i % 9, 1),
                first_seen=date(2026, 1 + i % 9, 2),
                full_text_read=i % 2 == 0,
            )
        )
    detections = []
    for i in range(n_detections):
        s = src.add(f"tech/detection-{i}", kind="aggregator")
        detections.append(
            DetectionRow(
                id=f"20000000-0000-4000-8000-{i:012d}",
                technology=("Salesforce", "Synthetic Tool")[i % 2]
                if i < 2
                else f"Synthetic Tool {i}",
                first_seen="2025-01",
                last_seen="2026-08",
                source_url=s.url,
                source_code=None,
            )
        )
    first = facts[0] if facts else None
    narrative_codes = (evidence[0].source_code,) if evidence and evidence[0].source_code else ()
    if not narrative_codes:
        narrative_codes = (src.add("about").code,)
    sections = [
        SectionRow(
            id="s-narr-1",
            kind="narrative",
            position=0,
            codes=narrative_codes,
            fact_ids=(first.id,) if first and first.product_grade == "confirmed" else (),
            text=f"{name} is a synthetic account used only for testing the builders.",
        ),
        SectionRow(
            id="s-cav-1",
            kind="caveat",
            position=0,
            codes=(),
            text="Synthetic caveat: findings come from public sources only.",
        ),
        SectionRow(
            id="s-hyp-1",
            kind="hypothesis",
            position=0,
            codes=narrative_codes,
            text="Synthetic hypothesis about the platform roadmap.",
            confirming_question="Is the synthetic roadmap funded this year?",
        ),
        SectionRow(
            id="s-fcq-1",
            kind="first_call_question",
            position=0,
            codes=(),
            text="Which synthetic team owns the platform?",
        ),
        SectionRow(
            id="s-dec-1",
            kind="decision_change",
            position=0,
            codes=narrative_codes,
            text="A confirmed synthetic budget would change the plan.",
        ),
        SectionRow(
            id="s-voice-1",
            kind="executive_voice",
            position=0,
            codes=narrative_codes,
            text="Synthetic quote: we are investing in service.",
            speaker="Synthetic Chief Executive",
        ),
    ]
    lines, tally = tracker()
    n_in = len(people)
    content = SnapshotContent(
        tenant_id=tenant,
        account_id=account_id,
        run_id=run,
        account_name=name,
        as_of=AS_OF,
        is_salesforce_partner=partner,
        forbidden_terms=("Synthetic Other Account Inc", "other.example"),
        facts=tuple(facts),
        evidence=tuple(evidence),
        sources=tuple(src.rows),
        sections=tuple(sections),
        people=tuple(people),
        people_names=tuple(sorted(names)),
        people_counts=PeopleCounts(
            summary=(
                ("in scope", "", n_in),
                ("excluded", "security function", 1),
                ("merged as duplicates", "", 1),
            ),
            sentences=(f"{n_in} people in scope of {n_in + 1} distinct people found",),
            in_scope=n_in,
        ),
        findings=tuple(findings),
        postings=tuple(postings),
        jobs_counts=JobsCounts(
            lines=(
                ("pulled", "Records pulled", n_postings + 2, "raw records before dedupe"),
                ("matched", "Postings naming a platform", n_postings, "postings analysed"),
            ),
            sentences=(f"{n_postings} postings read of {n_postings} matched",),
        ),
        detections=tuple(detections),
        tracker=lines,
        tracker_tally=tally,
        snapshot_counts={
            "facts": len(facts),
            "fact_evidence": len(evidence),
            "sources": len(src.rows),
            "report_sections": len(sections),
            "tech_detections": len(detections),
        },
    )
    return content


def sparse(name: str = "Synthetic Sparse Co") -> SnapshotContent:
    """A run with nothing found: no facts, people, postings or detections; narrative and tracker
    only (every empty section must render an explicit "None found")."""
    base = account(name, "sparse.example", n_facts=0, n_people=0, n_postings=0, n_detections=0)
    return base.model_copy(update={"people_counts": None, "jobs_counts": None, "people_names": ()})


def rich(
    name: str = "Synthetic Rich Corp", host: str = "rich.example", scale: int = 1
) -> SnapshotContent:
    """Many rows, long descriptions, Unicode, right-to-left text, emoji, control characters and
    formula-like strings (all must be rendered safely)."""
    return account(
        name,
        host,
        n_facts=24 * scale,
        n_people=30 * scale,
        n_postings=40 * scale,
        n_detections=12 * scale,
        long_text=True,
        hostile_text=True,
    )


def outage() -> SnapshotContent:
    """A provider outage: the people and jobs rows are Blocked with a reason in the tracker."""
    blocked = {k: Status.BLOCKED for k in ("job_board_by_lane", "job_postings_12_months")}
    lines, tally = tracker(blocked)
    base = account("Synthetic Outage Co", "outage.example", n_postings=0)
    return base.model_copy(update={"tracker": lines, "tracker_tally": tally, "jobs_counts": None})
