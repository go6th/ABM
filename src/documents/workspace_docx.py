"""Workspace import, phase 3 (2026-11-03): the Word deliverables of an uploaded workspace zip
(the Account Summary and the Sources and Evidence file) into the tables that hold a research's
own findings.

The Word parser stores each table of the file as rows (``research_files.parsers.documents``)
and each section as prose under its heading. This module reads those rows and sections back
for the members whose role is ``deliverable_summary`` or ``deliverable_evidence``
(``documents.workspace``) and plans what each becomes:

* the source register (``ID | Source | Domain`` in the Summary, ``ID | Source | Type | Date |
  How retrieved | What it supports`` in the Evidence file) -> ``sources`` through
  ``db.source_ids.import_source``, the file's code kept as an alias so a citation ``(S12)`` in
  the prose resolves to the registry's code;
* the signals tables (``Finding | Grade | Lanes | Date | Sources``), the news table (``Date |
  What happened | Read depth | Source | Why it matters``), the platform picture (``Item | What
  we found | Grade | Reasoning and sources``), the profile findings and the status changes ->
  ``facts`` through ``grading.repo.record_imported_fact``, graded as the file grades them;
* the tracker tables (``Source type | Status | Priority | Needs | What was done | What
  remains``) -> ``coverage_rows`` of the file-import run through ``tracker.repo``;
* the prose (the account in plain words, what stands out, the lane meanings, how this could
  be wrong, how to sharpen it, the baseline, the hypotheses with their confirming questions,
  the cautions, the first-conversation questions, the people interpretation) ->
  ``report_sections`` through ``agents.production.state.import_sections``;
* the key people table's notes -> ``people.note`` of the workbook's people (merged before the
  people import by ``documents.workspace_import``).

The planning is pure (``plan``); ``write`` stores the plan in the caller's transaction. No
model is called. Private unless the file is authoritative (an admin's mark, or the product's
own package with ``MANIFEST.json``), when the grades may be stated.

GUARANTEE(test_a_workspace_zip_imports_each_deliverable_row_once_and_skips_build_files)
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import partial
from typing import Any
from uuid import UUID

from documents.deliverables import _find, norm, segments
from documents.extract import slug

_URL_SUFFIX = re.compile(r"\s*\((https?://[^\s)]+)\)\s*$")
_CITES = re.compile(r"\(((?:[SJ]\d+)(?:\s*,\s*[SJ]\d+)*)\)")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})|(\d{1,2}) (\w{3}) (\d{4})")
# a Domain cell that names a host (``careers.ucb.com``), not a label like "Internal"
_HOST = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$", re.IGNORECASE)
_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
GRADES = {
    "confirmed": "confirmed",
    "probable": "probable",
    "hypothesis": "hypothesis",
    "lead": "lead",
}
STATUSES = {
    "done": "done",
    "partial": "partial",
    "not done": "not_done",
    "blocked": "blocked",
    "none": "none",
}


@dataclass
class DocxPlan:
    """What the Word deliverables become (see the module docstring)."""

    sources: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    tracker: list[dict[str, Any]] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)  # person name (case-folded) -> note
    counts: Counter[str] = field(default_factory=Counter)


def split_link(cell: str) -> tuple[str, str | None]:
    """``"Title (https://...)"`` as (title, url); a cell without a link keeps the whole text."""
    m = _URL_SUFFIX.search(cell or "")
    if not m:
        return (cell or "").strip(), None
    return cell[: m.start()].strip(), m.group(1)


def _valid_url(url: str) -> bool:
    """Whether the source register accepts ``url`` (``urls.normalize_url`` raises otherwise)."""
    from account_intelligence.urls import normalize_url

    try:
        normalize_url(url)
    except ValueError:
        return False
    return True


def parse_date(value: str | None) -> date | None:
    """``2026-09-21`` or ``21 Sep 2026`` (a date range keeps its first date); else None."""
    m = _DATE.search(value or "")
    if not m:
        return None
    try:
        if m.group(1):
            return date.fromisoformat(m.group(1))
        month = _MONTHS.get(m.group(3).casefold()[:3])
        return date(int(m.group(4)), month, int(m.group(2))) if month else None
    except ValueError:
        return None


def cited(text_value: str) -> list[str]:
    """The source codes a sentence cites, as written: ``(S2, S3)`` -> ["S2", "S3"]."""
    out: list[str] = []
    for m in _CITES.finditer(text_value or ""):
        out.extend(c.strip() for c in m.group(1).split(",") if c.strip().startswith("S"))
    return list(dict.fromkeys(out))


def _rows_of(passages: Sequence[Any]) -> dict[tuple[str, str], list[dict[str, str]]]:
    """The Word tables' rows per (member, table) as ``column -> value`` records."""
    tables: dict[tuple[str, str], list[dict[str, str]]] = {}
    for p in passages:
        location = dict(p.location or {})
        key = (str(location.get("member") or ""), str(location.get("table") or ""))
        tables.setdefault(key, []).append(dict(segments(p.content)))
    return tables


def _kind_of(columns: Sequence[str]) -> str | None:
    """Which deliverable table these column names are, else None."""
    cols = [norm(c) for c in columns]

    def has(*names: str) -> bool:
        return all(any(c == n or c.startswith(n) for c in cols) for n in names)

    if has("id", "source") and (has("domain") or has("how retrieved")):
        return "sources"
    # the signal tables lead with "Finding" (the reference) or "Signal" (UCB, 2026-11-04)
    if has("grade", "lanes") and (has("finding") or has("signal")):
        return "signals"
    if has("date", "what happened", "why it matters"):
        return "news"
    if has("item", "what we found", "grade") or has("lane", "what we see", "grade"):
        return "platform"
    if has("what the profiles show", "grade") and (has("topic") or has("finding")):
        return "profiles"
    if has("person", "was", "now"):
        return "status_changes"
    if has("source type", "status", "what was done"):
        return "tracker"
    if has("name", "title as reported") and has("note"):
        return "key_people"
    return None


def _value(record: Mapping[str, str], *names: str) -> str:
    col = _find(list(record), names)
    return (record.get(col) or "").strip() if col else ""


def _tier(table_name: str) -> str:
    name = table_name.casefold()
    if "tier 1" in name:
        return "tier_1"
    if "tier 2" in name:
        return "tier_2"
    if "tier 3" in name:
        return "tier_3"
    if "context" in name:
        return "context"
    return "signals"


def _tracker_key(label: str, rows: Sequence[Any]) -> str | None:
    """The checklist row whose label is the table's "Source type" (exact, then a prefix)."""
    wanted = " ".join(label.casefold().split())
    for row in rows:
        if " ".join(row.label.casefold().split()) == wanted:
            return str(row.key)
    for row in rows:
        have = " ".join(row.label.casefold().split())
        if wanted.startswith(have[:25]) or have.startswith(wanted[:25]):
            return str(row.key)
    return None


SECTION_KINDS: tuple[tuple[str, str], ...] = (
    ("what stands out", "stands_out"),
    ("what it likely means", "lane_meaning"),
    ("how this could be wrong", "how_wrong"),
    ("simplest way to sharpen", "sharpen"),
    ("the account in plain words", "narrative"),
    ("the account picture", "narrative"),
    ("company and transformation baseline", "baseline"),
    ("hypotheses to test", "hypothesis"),
    ("cautions for sensitive", "caution"),
    ("questions for a first conversation", "first_call_question"),
    ("interpretation", "interpretation"),
)


def lane_id(value: str | None) -> str | None:
    """The lane id (``config/job_lanes.yaml``) a cell names, else None ("All lanes" is none)."""
    from jobs.config import load_jobs_config

    words = (value or "").casefold()
    if not words or "all lanes" in words:
        return None
    for lane in load_jobs_config().lanes:
        names = [lane.id.replace("_", " "), lane.label.split("(")[0].strip(), *lane.platforms]
        if any(n.casefold() in words for n in names if n):
            return str(lane.id)
    return None


def _section_kind(heading_path: str | None) -> str | None:
    path = (heading_path or "").casefold()
    for needle, kind in SECTION_KINDS:
        if needle in path:
            return kind
    return None


def plan(
    row_passages: Sequence[Any], section_passages: Sequence[Any], checklist_rows: Sequence[Any]
) -> DocxPlan:
    """What the Word deliverables' rows and sections become (pure; see the module docstring).
    ``checklist_rows`` are the configured coverage rows (key and label)."""
    out = DocxPlan()
    for (member, table), records in _rows_of(row_passages).items():
        if not records:
            continue
        kind = _kind_of(list(records[0]))
        if kind is None:
            out.counts["docx_tables_unknown"] += 1
            continue
        out.counts[f"docx_{kind}_rows"] += len(records)
        for n, r in enumerate(records):
            where = {"member": member, "table": table, "row": n + 2}
            if kind == "sources":
                code = _value(r, "id")
                title, url = split_link(_value(r, "source"))
                domain = _value(r, "domain")
                host = domain.strip().rstrip("/")
                if not url and _valid_url(host):
                    url = host  # the Domain column holds the full address (owner files)
                elif not url and _HOST.match(host):
                    url = f"https://{host}/"
                # a row whose Domain is a label ("6th agents data pull", "Internal") has no web
                # address; one bad address must not roll back the whole import (UCB 2026-11-04)
                if url and not _valid_url(url):
                    url = None
                if not url or not code:
                    out.counts["docx_sources_no_link"] += 1
                    continue
                out.sources.append(
                    {
                        "alias": code,
                        "url": url,
                        "title": title or None,
                        "publisher": domain or None,
                        "published_at": parse_date(_value(r, "date")),
                        "kind": _value(r, "type"),
                        "how_retrieved": _value(r, "how retrieved") or None,
                        "what_it_supports": _value(r, "what it supports") or None,
                        "where": where,
                    }
                )
            elif kind == "signals":
                finding = _value(r, "finding", "signal")
                sources = _value(r, "sources", "source")
                if finding:
                    tier = _tier(table)
                    out.facts.append(
                        {
                            "attribute_key": f"signals.{tier}.{slug(finding)[:40]}",
                            "value": finding,
                            "grade": GRADES.get(_value(r, "grade").casefold(), "lead"),
                            "lane": lane_id(_value(r, "lanes")),
                            "fields": {
                                "tier": tier,
                                "lanes": _value(r, "lanes"),
                                "date": _value(r, "date"),
                                "sources": sources,
                            },
                            "cites": cited(f"({sources})"),
                            "flags": ("key_finding",) if tier == "tier_1" else (),
                            "where": where,
                        }
                    )
            elif kind == "news":
                what = _value(r, "what happened")
                if what:
                    out.facts.append(
                        {
                            "attribute_key": f"news.company_news.{slug(what)[:40]}",
                            "value": what,
                            "grade": "confirmed"
                            if _value(r, "read depth").casefold().startswith("read")
                            else "lead",
                            "fields": {
                                "date": _value(r, "date"),
                                "read_depth": _value(r, "read depth"),
                                "sources": _value(r, "source"),
                                "why_it_matters": _value(r, "why it matters"),
                            },
                            "cites": cited(f"({_value(r, 'source')})"),
                            "flags": (),
                            "where": where,
                        }
                    )
            elif kind == "platform":
                item = _value(r, "item", "lane")
                found = _value(r, "what we found", "what we see")
                if item and found:
                    out.facts.append(
                        {
                            "attribute_key": f"platform.{slug(item)[:40]}",
                            "value": f"{item}: {found}"[:2000],
                            "grade": GRADES.get(_value(r, "grade").casefold(), "lead"),
                            "lane": lane_id(item),
                            "fields": {
                                "item": item,
                                "reasoning": _value(r, "reasoning"),
                            },
                            "cites": cited(_value(r, "reasoning"))
                            or cited(f"({_value(r, 'source')})"),
                            "flags": (),
                            "where": where,
                        }
                    )
            elif kind == "profiles":
                topic = _value(r, "topic", "finding")
                shows = _value(r, "what the profiles show")
                if topic and shows:
                    out.facts.append(
                        {
                            "attribute_key": f"profiles.findings.{slug(topic)[:40]}",
                            "value": f"{topic}: {shows}"[:2000],
                            "grade": GRADES.get(_value(r, "grade").casefold(), "lead"),
                            "fields": {
                                "topic": topic,
                                "interpretation": _value(r, "our interpretation", "interpretation"),
                            },
                            "cites": [],
                            "flags": (),
                            "where": where,
                        }
                    )
            elif kind == "status_changes":
                person = _value(r, "person")
                now_text = _value(r, "now")
                if person and now_text:
                    out.facts.append(
                        {
                            "attribute_key": f"news.leadership_changes.{slug(person)[:40]}",
                            "value": f"{person}: {now_text}"[:2000],
                            "grade": "confirmed",
                            "fields": {
                                "person": person,
                                "was": _value(r, "was"),
                                "sources": _value(r, "source"),
                            },
                            "cites": cited(f"({_value(r, 'source')})"),
                            "flags": (),
                            "where": where,
                        }
                    )
            elif kind == "tracker":
                label = _value(r, "source type")
                status = STATUSES.get(_value(r, "status").casefold())
                key = _tracker_key(label, checklist_rows) if label else None
                if key and status:
                    out.tracker.append(
                        {
                            "row_key": key,
                            "status": status,
                            "needs": _value(r, "needs"),
                            "done": _value(r, "what was done"),
                            "what_remains": _value(r, "what remains"),
                        }
                    )
                else:
                    out.counts["docx_tracker_unmatched"] += 1
            elif kind == "key_people":
                name = _value(r, "name")
                note = _value(r, "note")
                if name and note:
                    out.notes.setdefault(name.casefold(), note[:2000])

    # prose: one section per paragraph, kind by heading; a hypothesis takes the "Confirming
    # question" paragraph that follows it
    pending_hypothesis: dict[str, Any] | None = None
    for p in section_passages:
        # a stored passage may hold several short sections, each led by its own heading line
        # (``documents.chunking`` merges them); the heading line switches the kind
        kind = _section_kind(getattr(p, "heading_path", None))
        lane = None
        for para in (x.strip() for x in str(p.content or "").split("\n")):
            if not para or para.startswith("[table at block"):
                continue
            if len(para) <= 80 and not para.endswith((".", "?", ")")):
                switched = _section_kind(para)
                if switched is not None:
                    kind = switched
                    pending_hypothesis = None
                    continue
            if kind is None:
                continue
            if kind == "hypothesis":
                if para.casefold().startswith("confirming question"):
                    if pending_hypothesis is not None:
                        pending_hypothesis["confirming_question"] = para.split(":", 1)[-1].strip()
                        pending_hypothesis = None
                    continue
                pending_hypothesis = {
                    "kind": kind,
                    "text": para,
                    "cited_source_codes": cited(para),
                }
                out.sections.append(pending_hypothesis)
                continue
            if kind == "lane_meaning":
                # the paragraph leads with its lane as written ("Salesforce (interpretation).");
                # the row keeps the lane id (ck_report_sections_lane), None when none matches
                head = para.split(".", 1)[0].strip()
                lane = lane_id(head) if len(head) <= 60 else None
            out.sections.append(
                {"kind": kind, "text": para, "cited_source_codes": cited(para), "lane": lane}
            )
    out.counts["docx_sections"] = len(out.sections)
    return out


async def write(
    s: Any,
    *,
    tenant_id: UUID,
    account_id: UUID,
    run_id: UUID,
    document_id: UUID,
    docx_plan: DocxPlan,
    authoritative: bool,
    account_domain: str,
    account_name: str | None,
    now: Any,
) -> Counter[str]:
    """Store a plan in the caller's transaction: sources first (so citations resolve), then the
    facts, the sections and the tracker. Returns the counts written."""
    from account_intelligence.db.enums import SourceKind
    from account_intelligence.db.source_ids import import_source
    from account_intelligence.grading.ports import RunInfo
    from account_intelligence.grading.repo import record_imported_fact
    from account_intelligence.tracker.config import load_checklist, load_tracker_config
    from account_intelligence.tracker.repo import CoverageRepo
    from agents.production.state import import_sections
    from signals.events import ImportedSignal, import_events

    counts: Counter[str] = Counter()
    visibility = "public" if authoritative else "private"
    codes: dict[str, str] = {}
    urls: dict[str, str] = {}
    for item in docx_plan.sources:
        urls[str(item["alias"])] = str(item["url"])[:2000]
        kind = (
            SourceKind.PRIMARY
            if "primary" in str(item["kind"]).casefold()
            else SourceKind.SECONDARY
        )
        source = await s.run_sync(
            partial(
                import_source,
                account_id=account_id,
                account_run_id=run_id,
                url=str(item["url"])[:2000],
                kind=kind,
                title=item["title"],
                publisher=item["publisher"],
                published_at=item["published_at"],
                alias=item["alias"],
                how_retrieved=item["how_retrieved"],
                what_it_supports=item["what_it_supports"],
            )
        )
        codes[str(item["alias"])] = str(source.source_code)
        counts["sources_written"] += 1
    info = RunInfo(tenant_id, account_id, run_id, now, account_domain, account_name)
    signals: list[ImportedSignal] = []
    for f in docx_plan.facts:
        cites = [c for c in f.get("cites", ()) if c in codes]
        resolved = [codes[c] for c in cites]
        fields = {**f.get("fields", {}), "source_codes": resolved}
        fact_id, created = await record_imported_fact(
            s,
            run=info,
            attribute_key=str(f["attribute_key"]),
            value=str(f["value"])[:2000],
            quote=str(f["value"])[:600],
            document_id=document_id,
            location=dict(f["where"]),
            grade=str(f["grade"]),
            visibility=visibility,
            flags=("workspace_import", *f.get("flags", ())),
            config_hash="workspace-docx-import-v1",
            fields=fields,
            lane=f.get("lane"),
            confirming_question=f.get("confirming_question"),
        )
        counts["facts_written"] += int(created)
        key = str(f["attribute_key"])
        if key.startswith("signals."):
            # the Summary's signal tables become signal events, so the Signals screen and the
            # account page show every signal the file states (owner's rule 2026-11-03)
            tier = key.split(".")[1] if key.count(".") >= 2 else "tier_3"
            signals.append(
                ImportedSignal(
                    fact_id=fact_id,
                    attribute_key=key,
                    title=str(f["value"]),
                    grade=str(f["grade"]),
                    bucket=int(tier[-1]) if tier[-1].isdigit() else 3,
                    detected_at=now,
                    source_url=urls.get(cites[0]) if cites else None,
                    source_code=resolved[0] if resolved else None,
                )
            )
    if signals:
        counts["signals_written"] = await import_events(s, account_id, signals)
    sections = [
        {
            **sec,
            "cited_source_codes": [
                codes[c] for c in sec.get("cited_source_codes", ()) if c in codes
            ],
        }
        for sec in docx_plan.sections
    ]
    counts["sections_written"] = await import_sections(
        s, tenant_id, account_id, run_id, sections, visibility=visibility
    )
    if docx_plan.tracker:
        repo = CoverageRepo(s)
        cfg = load_checklist()
        await repo.seed(
            run_id, tenant_id, cfg.rows, cfg.version, cfg.sha256,
            load_tracker_config().d_12_completion_policy, {},
        )  # fmt: skip
        counts["tracker_written"] = await repo.import_rows(run_id, tenant_id, docx_plan.tracker)
    counts.update(docx_plan.counts)
    return counts
