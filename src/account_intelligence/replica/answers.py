"""Workspace answers for Ask: retrieve stored rows, compose an answer that cites them, and say
what the answer used.

Answers come only from what the workspace holds: stored facts, job postings, people, signals,
technology, and the client's uploaded files. Nothing here calls the web or a
data tool (GUARANTEE(test_workspace_answer_makes_no_paid_call)). Every statement that rests on a
row carries a numbered citation to that row, numbered in order of first appearance.

Two composers produce the same output shape:

* ``compose_local``: written in code from the retrieved rows. Used when no model key is set
  (local development, tests) and as the fallback when the model reply cannot be used.
* ``stream_with_model`` (``replica.model_answer``): Claude writes the prose from the same
  numbered rows; its citations are checked against those rows before anything is shown.

Answer text uses two inline marks, parsed into parts for the client: ``**bold**`` and ``[n]``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.replica.current import (
    NEWEST_DETECTION,
    NEWEST_FACT,
    NEWEST_PERSON,
    NEWEST_POSTING,
)
from account_intelligence.replica.settings import CADENCE_DAYS
from people.config import internal_people_sql

# ---------------------------------------------------------------------------------------------
# Vocabulary

LANE_LABEL = {
    "salesforce": "Salesforce",
    "servicenow": "ServiceNow",
    "sap": "SAP",
    "tableau": "Tableau",
    "data_and_ai": "Data and AI",
    "workday": "Workday",
    "oracle": "Oracle",
}

# Topic words and the terms that mark a row as being about that topic.
TOPICS: dict[str, tuple[str, tuple[str, ...]]] = {
    "salesforce": (
        "Salesforce",
        ("salesforce", "crm", "mulesoft", "service cloud", "data cloud", "customer data"),
    ),
    "servicenow": (
        "ServiceNow",
        ("servicenow", "service management", "itsm", "enterprise platforms"),
    ),
    "sap": ("SAP", ("sap", "s/4hana", "finance systems", "erp")),
    "tableau": ("Tableau", ("tableau", "reporting", "analytics platform")),
    "data_and_ai": (
        "data and AI",
        ("data", "analytics", "machine learning", " ai", "ai ", "databricks", "snowflake"),
    ),
    "workday": ("Workday", ("workday",)),
}

# Checked in order: a question about roles or hiring is a hiring question even when it also asks
# who to call (the answer then adds first call questions).
INTENT_WORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "first_call",
        (
            "first call",
            "questions for",
            "call prep",
            "opener",
            "prep:",
            "prep for",
            "talking points",
        ),
    ),
    (
        "hiring",
        (
            "role",
            "roles",
            "hiring",
            "hired",
            "hire",
            "posted",
            "posting",
            "postings",
            "job",
            "jobs",
            "openings",
            "recruit",
        ),
    ),
    (
        "people",
        (
            "who owns",
            "who is",
            "who are",
            "who should",
            "owner",
            "owns",
            "decision maker",
            "leader",
            "contact",
            "people at",
        ),
    ),
    (
        "changes",
        (
            "what changed",
            "changed",
            "what's new",
            "what is new",
            "anything new",
            "since my last",
            "this week",
            "updates",
        ),
    ),
    (
        "technology",
        (
            "technology",
            "tech stack",
            "stack",
            "use ",
            "uses",
            "using",
            "platform",
            "tools",
            "software",
        ),
    ),
]

CATEGORY = {
    "people": "people",
    "hiring": "hiring",
    "first_call": "first_call",
    "technology": "technology",
    "changes": "changes",
    "other": "other",
}

NUM_WORD = {
    1: "One",
    2: "Two",
    3: "Three",
    4: "Four",
    5: "Five",
    6: "Six",
    7: "Seven",
    8: "Eight",
    9: "Nine",
}
COUNT_WORD = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}

GRADE_LABEL = {
    "confirmed": "Confirmed",
    "probable": "Probable",
    "hypothesis": "Hypothesis",
    "lead": "Lead",
    "internal": "Internal",
}

NO_ANSWER = (
    "I searched your {n} accounts and found nothing specific on that yet. Try asking about hiring,"
    " people, technology or what changed. If you need something newer, I can check the web, but"
    " only if you confirm."
)


def day_month(value: date | datetime | None) -> str:
    """'11 Oct' (no leading zero)."""
    if value is None:
        return ""
    return f"{value.day} {value.strftime('%b')}"


# ---------------------------------------------------------------------------------------------
# Rows


@dataclass
class Acct:
    """One account of the workspace with what the answer needs about it."""

    id: UUID
    domain: str
    name: str
    lanes: list[str]
    cadence: str | None
    last_refresh: datetime | None
    refreshes: int

    def refreshed_days(self, now: datetime) -> int | None:
        """Days since the account was last researched; None when it never was."""
        return None if self.last_refresh is None else max(0, (now - self.last_refresh).days)


@dataclass
class Row:
    """One citable stored row."""

    key: str
    kind: str
    account: Acct
    text: str
    title: str
    meta: str
    when: datetime | None
    url: str | None
    grade: str | None = None
    lane: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def haystack(self) -> str:
        """Every searchable field of the row, lower case and space padded for word matches."""
        extra = " ".join(str(v) for k, v in self.extra.items() if k != "sheet")
        return f" {self.text} {self.title} {self.meta} {self.lane or ''} {extra} ".lower()


@dataclass
class Workspace:
    """Everything retrieved for one question, already scoped to the tenant (RLS) and user."""

    accounts: list[Acct]
    facts: list[Row]
    jobs: list[Row]
    people: list[Row]
    signals: list[Row]
    tech: list[Row]
    questions: dict[UUID, list[str]]
    # Internal facts from the client's uploaded files (labelled "From your files"), the research
    # tracker of each account's latest run, and the text of previous deliverables
    files: list[Row] = field(default_factory=list)
    # passages of the client's own documents retrieved for THIS question (hybrid search,
    # documents.retrieve; filled by the Ask route once the question is known)
    passages: list[Row] = field(default_factory=list)
    # the best-matching uploaded file, whole, when it fits the budget (take_whole_file)
    whole_files: list[Row] = field(default_factory=list)
    # the ingested tables of the caller's files the model may query (table_rows; the Ask
    # route fills it, with the table tools, for a tabular or mixed question)
    tables: list[Row] = field(default_factory=list)
    tracker: list[Row] = field(default_factory=list)
    reports: list[Row] = field(default_factory=list)


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    return None


_CLOSED_WORDS = re.compile(
    r"\b(closed|history|historical|past|previous|former|ever|expired|no longer|were|over time"
    r"|all (?:the |of the )?(?:postings|roles|jobs)|since \d{4}|last year|earlier)\b",
    re.IGNORECASE,
)


def wants_closed(question: str) -> bool:
    """Whether a question asks about postings that are no longer open (history, closed, past):
    only then do closed postings join the rows, so a plain question still shows what is open."""
    return bool(_CLOSED_WORDS.search(question))


async def load_workspace(
    session: Any, user_id: str, account_id: UUID | None = None, *, include_closed: bool = False
) -> Workspace:
    """Read the rows an answer may cite (tenant scoped by RLS).
    Closed postings are read only when the question asks about them (``include_closed``)."""
    acct_rows = (
        await session.execute(
            text(
                "SELECT a.id, a.domain, coalesce(a.name, a.domain) AS name, a.lanes,"
                " a.refresh_cadence,"
                " (SELECT max(j.finished_at) FROM research_jobs j WHERE j.account_id = a.id AND"
                " j.status = 'done')"
                "   AS job_done,"
                " (SELECT max(r.finished_at) FROM account_runs r WHERE r.account_id = a.id"
                "   AND r.status IN ('COMPLETED', 'PARTIAL')) AS run_done,"
                " (SELECT count(*) FROM account_runs r WHERE r.account_id = a.id"
                "   AND r.status IN ('COMPLETED', 'PARTIAL')) AS runs"
                " FROM accounts a WHERE a.deleted_at IS NULL ORDER BY coalesce(a.name, a.domain)"
            )
        )
    ).all()
    accounts = [
        Acct(
            id=r.id,
            domain=r.domain,
            name=r.name,
            lanes=list(r.lanes or []),
            cadence=r.refresh_cadence,
            last_refresh=max(
                [d for d in (_as_dt(r.job_done), _as_dt(r.run_done)) if d], default=None
            ),
            refreshes=int(r.runs or 0),
        )
        for r in acct_rows
    ]
    by_id = {a.id: a for a in accounts}
    scope = {account_id} if account_id else set(by_id)

    facts: list[Row] = []
    for r in (
        await session.execute(
            text(
                "SELECT f.id, f.account_id, f.attribute_key, coalesce(f.value_text,"  # nosec B608 -- fixed SQL fragments; values are bound.
                " f.value_json->>'text') AS v,"
                " f.product_grade, f.lane, f.flags, f.created_at, f.visibility,"
                " e.url, e.evidence_date, s.title AS source_title"
                " FROM facts f"
                " LEFT JOIN LATERAL (SELECT url, evidence_date, source_id FROM fact_evidence x"
                "   WHERE x.fact_id = f.id ORDER BY evidence_date DESC NULLS LAST LIMIT 1) e ON"
                " true"
                " LEFT JOIN sources s ON s.id = e.source_id"
                " WHERE f.status = 'current' AND f.superseded_at IS NULL AND f.visibility ="
                " 'public'" + NEWEST_FACT + " ORDER BY f.created_at DESC LIMIT 2000"
            )
        )
    ).all():
        acct = by_id.get(r.account_id)
        if acct is None or r.account_id not in scope or not r.v:
            continue
        when = _as_dt(r.evidence_date) or _as_dt(r.created_at)
        title = r.source_title or f"{acct.name} source"
        facts.append(
            Row(
                key=f"fact:{r.id}",
                kind="fact",
                account=acct,
                text=str(r.v),
                title=title,
                meta=f"{acct.name} · {day_month(when)}"
                if acct.name not in title
                else day_month(when),
                when=when,
                url=r.url,
                grade=r.product_grade,
                lane=r.lane,
                extra={"attr": r.attribute_key, "key_finding": "key_finding" in (r.flags or [])},
            )
        )

    jobs: list[Row] = []
    for r in (
        await session.execute(
            text(
                "SELECT j.id, j.account_id, j.title, j.url, j.posted_at, j.first_seen, j.lanes,"  # nosec B608 -- fixed SQL fragments; values are bound.
                " j.status, j.location, j.created_at"
                " FROM job_postings j WHERE true"
                + ("" if include_closed else " AND j.status <> 'closed'")
                + NEWEST_POSTING
                + " ORDER BY coalesce(j.posted_at, j.first_seen) DESC NULLS LAST"
                " LIMIT 3000"
            )
        )
    ).all():
        acct = by_id.get(r.account_id)
        if acct is None or r.account_id not in scope or not r.title:
            continue
        when = _as_dt(r.posted_at) or _as_dt(r.first_seen) or _as_dt(r.created_at)
        closed = r.status == "closed"
        jobs.append(
            Row(
                key=f"job:{r.id}",
                kind="job",
                account=acct,
                text=f"{r.title} (closed)" if closed else str(r.title),
                title=f"{acct.name} careers posting",
                meta=f"{r.title} · {day_month(when)}" + (" · closed" if closed else ""),
                when=when,
                url=r.url,
                extra={
                    "lanes": " ".join(r.lanes or []),
                    "location": r.location or "",
                    "status": r.status or "",
                },
            )
        )

    people: list[Row] = []
    for r in (
        await session.execute(
            text(
                "SELECT p.id, p.account_id, coalesce(p.name, '[Name]') AS name, p.title,"  # nosec B608 -- fixed SQL fragments; values are bound.
                " p.priority,"
                " p.classification_json, p.created_at, s.title AS source_title, s.url AS source_url"
                " FROM people p LEFT JOIN sources s ON s.account_id = p.account_id AND"
                " s.source_code = p.source_ref"
                " WHERE NOT p.excluded AND p.merged_into IS NULL AND p.erased_at IS NULL"
                f" AND {internal_people_sql()}" + NEWEST_PERSON + " LIMIT 3000"
            )
        )
    ).all():
        acct = by_id.get(r.account_id)
        if acct is None or r.account_id not in scope or not r.title:
            continue
        cls = r.classification_json or {}
        when = _as_dt(r.created_at)
        people.append(
            Row(
                key=f"person:{r.id}",
                kind="person",
                account=acct,
                text=f"{r.name}, {r.title}",
                title=r.source_title or f"{acct.name} leadership page",
                meta=f"Retrieved {day_month(when)}",
                when=when,
                url=r.source_url,
                grade=cls.get("grade"),
                extra={"title": r.title, "area": cls.get("area", ""), "priority": r.priority or ""},
            )
        )

    signals: list[Row] = []
    for r in (
        await session.execute(
            text(
                "SELECT s.id, s.account_id, s.signal_type, s.title, s.grade, s.detected_at,"
                " (SELECT e.url FROM fact_evidence e WHERE e.fact_id = s.fact_id LIMIT 1) AS url"
                " FROM signal_events s ORDER BY s.detected_at DESC LIMIT 2000"
            )
        )
    ).all():
        acct = by_id.get(r.account_id)
        if acct is None or r.account_id not in scope:
            continue
        when = _as_dt(r.detected_at)
        signals.append(
            Row(
                key=f"signal:{r.id}",
                kind="signal",
                account=acct,
                text=str(r.title),
                title=f"{acct.name} signal",
                meta=f"{str(r.signal_type).capitalize()} · {day_month(when)}",
                when=when,
                url=r.url,
                grade=r.grade,
                extra={"type": r.signal_type},
            )
        )

    tech: list[Row] = []
    for r in (
        await session.execute(
            text(
                "SELECT t.id, t.account_id, t.technology, t.cross_check_result, t.created_at,"  # nosec B608 -- fixed SQL fragments; values are bound.
                " s.url, s.title AS source_title"
                " FROM tech_detections t LEFT JOIN sources s ON s.account_id = t.account_id AND"
                # the pipeline stores the page's address in source_ref; older rows hold the S-code
                " (s.source_code = t.source_ref OR s.url = t.source_ref) WHERE true"
                + NEWEST_DETECTION
                + " LIMIT 2000"
            )
        )
    ).all():
        acct = by_id.get(r.account_id)
        if acct is None or r.account_id not in scope:
            continue
        when = _as_dt(r.created_at)
        tech.append(
            Row(
                key=f"technology:{r.id}",
                kind="technology",
                account=acct,
                text=str(r.technology),
                title=r.source_title or f"{acct.name} technology detection",
                meta=f"Detected · {day_month(when)}",
                when=when,
                url=r.url,
                grade="confirmed" if r.cross_check_result == "confirmed" else "probable",
            )
        )

    questions: dict[UUID, list[str]] = defaultdict(list)
    for r in (
        await session.execute(
            text(
                "SELECT DISTINCT ON (account_id, position) account_id, text FROM report_sections"
                " WHERE kind = 'first_call_question' AND visibility = 'public'"
                " ORDER BY account_id, position, version DESC"
            )
        )
    ).all():
        questions[r.account_id].append(str(r.text))

    # the tenant's CURRENT exclusions (F1): an answer never cites a row that names an excluded
    # person or sits on an excluded company's domain, whenever the research ran
    from exports.readers.exclusions import text_excluded, with_known_names
    from tenancy import settings as tenant_settings

    rule = tenant_settings.ExclusionFilter.of((await tenant_settings.current(session)).settings)
    if rule.people or rule.companies:
        known = (await session.execute(text("SELECT name, profile_url FROM people"))).all()
        rule = with_known_names(rule, [(r.name, r.profile_url) for r in known])

        def _kept(rows: list[Row]) -> list[Row]:
            return [r for r in rows if not text_excluded(rule, r.text, r.title, url=r.url)]

        facts, jobs, people, signals, tech = (
            _kept(facts),
            _kept(jobs),
            _kept(people),
            _kept(signals),
            _kept(tech),
        )

    return Workspace(
        accounts=accounts,
        facts=facts,
        jobs=jobs,
        people=people,
        signals=signals,
        tech=tech,
        questions=questions,
        files=await _file_rows(session, by_id, scope),
        tracker=await _tracker_rows(session, by_id, scope),
        reports=await _report_rows(session, by_id, scope),
    )


async def _file_rows(session: Any, by_id: dict[UUID, Acct], scope: set[UUID]) -> list[Row]:
    """Internal facts read from the client's own uploaded files, with the file they came from
    (private to the workspace; never in a shareable file)."""
    out = []
    for r in (
        await session.execute(
            text(
                "SELECT f.id, f.account_id, coalesce(f.value_text, f.value_json->>'text') AS v,"
                " f.created_at, e.supporting_sentence, e.document_location,"
                " coalesce(d.name, 'an uploaded file') AS document"
                " FROM facts f JOIN LATERAL (SELECT supporting_sentence, document_location,"
                "   document_id FROM fact_evidence x WHERE x.fact_id = f.id"
                "   AND x.evidence_kind = 'document' LIMIT 1) e ON true"
                " LEFT JOIN documents d ON d.id = e.document_id"
                " WHERE f.status = 'current' AND f.superseded_at IS NULL"
                " AND f.visibility = 'private' AND f.account_id = ANY(:scope)"
                " ORDER BY f.created_at DESC LIMIT 1000"
            ),
            {"scope": list(scope)},
        )
    ).all():
        acct = by_id.get(r.account_id)
        if acct is None or r.account_id not in scope or not r.v:
            continue
        where = (r.document_location or {}).get("cite") if r.document_location else None
        when = _as_dt(r.created_at)
        out.append(
            Row(
                key=f"file:{r.id}",
                kind="file",
                account=acct,
                text=str(r.v),
                title="From your files: "
                + file_label(str(r.document), (r.document_location or {}).get("member")),
                meta=f"Internal · {where}" if where else "Internal · from your files",
                when=when,
                url=None,
                grade="internal",
                extra={"quote": r.supporting_sentence or ""},
            )
        )
    return out


async def passage_rows(
    session: Any, ws: Workspace, question: str, scope: set[UUID], embedder: Any | None = None
) -> list[Row]:
    """The passages of the caller's uploaded documents that best answer ``question`` (hybrid
    keyword and vector search), as citable "From your files" rows."""
    from documents.retrieve import retrieve

    by_id = {a.id: a for a in ws.accounts}
    out = []
    for p in await retrieve(session, question, sorted(scope & set(by_id)), embedder):
        acct = by_id.get(p.account_id)
        if acct is None:
            continue
        out.append(
            Row(
                key=f"passage:{p.key}",
                kind="file",
                account=acct,
                text=p.content,
                title=f"From your files: {file_label(p.document, p.member)}",
                meta=f"Internal · {p.cite}" if p.cite else "Internal · from your files",
                when=None,
                url=None,
                grade="internal",
                extra={"quote": p.content[:300], "passage": True},
            )
        )
    return out


def file_label(document: str, member: str | None) -> str:
    """A source's file as the chip names it: the file inside an archive first (``People.xlsx in
    Acme_workspace.zip``), so eleven sources from one zip are not eleven identical chips."""
    if not member:
        return document
    name = member.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return f"{name} in {document}" if name and name != document else document


# the deep-dive sheet a deliverable workbook of a workspace zip is (``documents.workspace``)
_ROLE_SHEET = {
    "deliverable_jobs": "jobs",
    "deliverable_people": "people",
    "deliverable_tech": "technology",
}


def member_role(member: str | None) -> str | None:
    """The role of a workspace zip member from its path (``documents.workspace.classify_member``
    under the folder that holds its ``deliverables/``), or None for a loose upload."""
    from documents.workspace import classify_member

    if not member:
        return None
    parts = [p for p in member.replace("\\", "/").split("/") if p and p != "."]
    for i, part in enumerate(parts[:-1]):
        if part.lower() == "deliverables":
            root = "/".join(parts[:i])
            return str(classify_member(member, f"{root}/" if root else ""))
    return None


def table_sheet(t: Any) -> str | None:
    """The deep-dive sheet a table is (``sheets.SHEETS`` / ``FILE_TABLES`` key): a research
    table by its client table kind, a zip's deliverable workbook by its role; None otherwise."""
    from account_intelligence.replica.sheets import FILE_TABLES

    if t.view is not None:
        return next((k for k, kind in FILE_TABLES.items() if kind == t.view.kind), None)
    return _ROLE_SHEET.get(member_role(t.member) or "")


def table_rows(ws: Workspace, tables: list[Any]) -> list[Row]:
    """The workspace's ingested tables (``documents.tables.list_tables``) as citable rows: the
    model cites the table it queried, and the answer's source list names the file. A table that
    is a deep-dive sheet (``table_sheet``) carries ``extra["sheet"]``: its citation opens that
    sheet (``carry_table_matches`` adds the answer's filter)."""
    by_id = {a.id: a for a in ws.accounts}
    out = []
    for t in tables:
        acct = by_id.get(t.account_id)
        if acct is None:
            continue
        # a research table that spans several accounts has no one account's sheet to open (its
        # ``account_id`` is only the first of them): it is cited without a deep-dive link
        many = t.view is not None and len(t.view.account_ids) != 1
        sheet = None if many else table_sheet(t)
        columns = ", ".join(c.raw for c in t.columns if not c.payload)[:400]
        out.append(
            Row(
                key=f"table:{t.id}",
                kind="file",
                account=acct,
                text=f"{t.document}, table {t.name}: {t.row_count} rows; columns: {columns}",
                title=(
                    f"From the research results: {t.name}"
                    if t.view
                    else f"From your files: {file_label(t.document, t.member)} (table {t.name})"
                ),
                meta=f"Internal · {t.row_count} rows, queried whole",
                when=None,
                url=None,
                grade="internal",
                extra={
                    "table": t.name,
                    "document": t.document,
                    "rows": t.row_count,
                    # one table holds every account's rows: it is cited whichever account the
                    # question names (pick_rows)
                    **({"research": True} if t.view else {}),
                    **({"sheet": {"key": sheet}} if sheet else {}),
                },
            )
        )
    return out


# the member file a passage of an uploaded archive came from (``documents.chunking.cite``):
# ``member`` on a row or a plain section, ``from.member`` on a section merged across units
_MEMBER = "coalesce(p.location->>'member', p.location->'from'->>'member')"


async def take_whole_file(
    session: Any,
    ws: Workspace,
    max_chars: int,
    prefer_summary: bool = False,
    accounts: Sequence[UUID] | None = None,
) -> None:
    """Give the model the whole of the file the question is about (owner decision 2026-10-17:
    an answer over a spreadsheet must count every row, as a file attached to a chat does).

    The file is the one the best-ranked retrieved passage came from. Its passages, in order,
    become one ``whole_file`` row (cited as one source) and leave ``ws.passages``; a file above
    ``max_chars`` stays as retrieved passages only, so a question never ships more than the
    budget to the model. Same tenant scope as retrieval (row level security).

    An uploaded archive is one document whose passages span every member file; each passage
    names its member in ``location`` (``member`` on a row, ``from.member`` on a merged section,
    see ``documents.chunking.cite``). There the file is the member the best passage came from:
    only its passages are loaded, measured against the budget and dropped from the retrieved
    list, and the row is named "<member> in <archive>". A document that is not an archive has
    no member and is read whole as before.

    ``prefer_summary`` (an open question, ``replica.open_question``; Ask insight-answers plan,
    2026-11-03): the account's Account Summary goes whole first (a workspace zip's
    ``deliverable_summary`` member, by its recorded role or its file name), then the best hit's
    file when it is another file and the rest of the budget holds it. An open question is built
    from the summary's signal tables and counts, which search alone did not reach. The summary is
    the one of the account the turn is about, in any workspace of any number of accounts:
    ``accounts`` (the chat's scoped account or the accounts the question names), else the best
    hit's account, else the workspace's only account; never another account's summary."""
    if max_chars <= 0:
        return
    # (document id, member, key suffix): the summary first when asked for, then the best hit
    targets: list[tuple[UUID, str | None, str]] = []
    hit = await _best_hit_member(session, ws)
    if prefer_summary:
        held = {a.id for a in ws.accounts}
        about = [a for a in accounts or () if a in held]
        if not about and hit is not None:
            about = [hit[2]]
        if not about and len(held) == 1:
            about = list(held)
        summary = await _summary_member(session, about) if about else None
        if summary is not None:
            targets.append((summary[0], summary[1], ":summary"))
    if hit is not None and all((hit[0], hit[1]) != (d, m) for d, m, _ in targets):
        targets.append((hit[0], hit[1], ""))
    left = max_chars
    loaded: list[Row] = []
    for doc_id, member, suffix in targets:
        got = await _whole_member(session, ws, doc_id, member, left, suffix)
        if got is None:
            continue
        row, taken, size = got
        loaded.append(row)
        left -= size
        ws.passages = [r for r in ws.passages if r.key not in taken]
    if loaded:
        ws.whole_files = loaded


async def _best_hit_member(session: Any, ws: Workspace) -> tuple[UUID, str | None, UUID] | None:
    """The document, member and account of the best-ranked retrieved passage."""
    if not ws.passages:
        return None
    parts = ws.passages[0].key.split(":")  # passage:<document id>:<seq>
    if len(parts) != 3:
        return None
    try:
        doc_id, seq = UUID(parts[1]), int(parts[2])
    except ValueError:
        return None
    member = (
        await session.execute(
            text(
                "SELECT " + _MEMBER + " AS member, p.account_id FROM document_passages p"  # nosec B608 -- fixed SQL fragments; values are bound.
                " WHERE p.document_id = :d AND p.seq = :s"
            ),
            {"d": doc_id, "s": seq},
        )
    ).first()
    if member is None:
        return None
    return doc_id, (str(member.member) if member.member else None), member.account_id


# an Account Summary by name: a workspace zip member or a loose upload (documents.workspace
# names it "<Account>_Account_Summary.docx")
_SUMMARY_NAME = "%account_summary%.docx"


async def _summary_member(session: Any, accounts: Sequence[UUID]) -> tuple[UUID, str | None] | None:
    """The newest Account Summary of these accounts, the first listed account preferred: a zip
    member whose recorded role is ``deliverable_summary`` or whose name says so, or a loose
    summary upload (any account's "<Account>_Account_Summary.docx")."""
    if not accounts:
        return None
    row = (
        await session.execute(
            text(
                "SELECT p.document_id, " + _MEMBER + " AS member FROM document_passages p"  # nosec B608 -- fixed SQL fragments; values are bound.
                " JOIN documents d ON d.id = p.document_id"
                " LEFT JOIN document_members m ON m.document_id = d.id"
                " AND m.member_path = " + _MEMBER + ""
                " WHERE p.account_id = ANY(:accts) AND d.status = 'indexed' AND ("
                " m.role = 'deliverable_summary'"
                " OR lower(coalesce(" + _MEMBER + ", '')) LIKE :name"
                " OR (" + _MEMBER + " IS NULL AND lower(d.name) LIKE :name))"
                " ORDER BY array_position(CAST(:accts AS uuid[]), p.account_id),"
                " d.created_at DESC, p.seq LIMIT 1"
            ),
            {"accts": list(accounts), "name": _SUMMARY_NAME},
        )
    ).first()
    if row is None:
        return None
    return UUID(str(row.document_id)), (str(row.member) if row.member else None)


async def _whole_member(
    session: Any,
    ws: Workspace,
    doc_id: UUID,
    member: str | None,
    max_chars: int,
    key_suffix: str,
) -> tuple[Row, set[str], int] | None:
    """One file (a document, or one member of an archive) as a ``whole_file`` row, the passage
    keys it covers and its size against the budget (its stored passages' characters); None when
    it is empty, over ``max_chars`` or outside the scope."""
    head = (
        await session.execute(
            text(
                "SELECT d.name, d.account_id, count(p.id) AS n,"  # nosec B608 -- fixed SQL fragments; values are bound.
                " coalesce(sum(length(p.content)), 0) AS chars"
                " FROM documents d JOIN document_passages p ON p.document_id = d.id"
                " AND " + _MEMBER + " IS NOT DISTINCT FROM CAST(:m AS text)"
                " WHERE d.id = :d GROUP BY d.id, d.name, d.account_id"
            ),
            {"d": doc_id, "m": member},
        )
    ).first()
    if head is None or not head.n or int(head.chars) > max_chars:
        return None
    acct = next((a for a in ws.accounts if a.id == head.account_id), None)
    if acct is None:
        return None
    rows = (
        await session.execute(
            text(
                "SELECT p.seq, p.content FROM document_passages p WHERE p.document_id = :d"  # nosec B608 -- fixed SQL fragments; values are bound.
                " AND " + _MEMBER + " IS NOT DISTINCT FROM CAST(:m AS text) ORDER BY p.seq"
            ),
            {"d": doc_id, "m": member},
        )
    ).all()
    body = "\n".join(str(r.content).strip() for r in rows if r.content)
    if not body.strip():
        return None
    archive = str(head.name or "an uploaded file")
    name = f"{member} in {archive}" if member else archive
    row = Row(
        key=f"whole:{doc_id}{key_suffix}",
        kind="file",
        account=acct,
        text=body,
        title=f"From your files: {name} (the whole file)",
        meta=f"Internal · {int(head.n)} passages, read whole",
        when=None,
        url=None,
        grade="internal",
        extra={
            "whole_file": True,
            "document": name,
            "lines": body.count("\n") + 1,
            **({"member": member} if member else {}),
        },
    )
    return row, {f"passage:{doc_id}:{r.seq}" for r in rows}, int(head.chars)


async def _tracker_rows(session: Any, by_id: dict[UUID, Acct], scope: set[UUID]) -> list[Row]:
    """Each account's research tracker from its latest finished run: what was covered and why
    a row was not."""
    from uuid import NAMESPACE_URL, uuid5

    from account_intelligence.tracker.config import load_checklist, load_tracker_config

    labels = {r.key: r.label for r in load_checklist().rows}
    reasons = load_tracker_config().reason_codes
    out = []
    for r in (
        await session.execute(
            text(
                "SELECT c.run_id, c.row_key, c.status, c.reason_code, r.account_id, r.finished_at"
                " FROM coverage_rows c JOIN account_runs r ON r.id = c.run_id"
                " WHERE r.id IN (SELECT DISTINCT ON (account_id) id FROM account_runs"
                "   WHERE status IN ('COMPLETED', 'PARTIAL', 'BUDGET_EXCEEDED')"
                "   AND graph_version NOT IN ('library-copy', 'hermetic-research')"
                "   ORDER BY account_id, finished_at DESC)"
                " AND c.status IS NOT NULL"
            )
        )
    ).all():
        acct = by_id.get(r.account_id)
        if acct is None or r.account_id not in scope:
            continue
        status = str(r.status).replace("_", " ")
        why = reasons.get(r.reason_code or "", "") if r.reason_code else ""
        label = labels.get(r.row_key, r.row_key)
        when = _as_dt(r.finished_at)
        out.append(
            Row(
                key=f"tracker:{uuid5(NAMESPACE_URL, f'{r.run_id}/{r.row_key}')}",
                kind="tracker",
                account=acct,
                text=f"{label}: {status}" + (f" ({why})" if why else ""),
                title=f"{acct.name} research tracker",
                meta=f"Coverage · {day_month(when)}",
                when=when,
                url=None,
                extra={"row": r.row_key},
            )
        )
    return out


async def _report_rows(session: Any, by_id: dict[UUID, Acct], scope: set[UUID]) -> list[Row]:
    """The text of each account's latest deliverables (the Summary's one minute version, its
    hypotheses and caveats), including a 6th Library copy."""
    out = []
    for r in (
        await session.execute(
            text(
                "SELECT DISTINCT ON (account_id, kind, position) id, account_id, kind, text,"
                " created_at FROM report_sections"
                " WHERE visibility = 'public'"
                " AND kind IN ('narrative', 'hypothesis', 'caveat', 'executive_voice')"
                " ORDER BY account_id, kind, position, version DESC, created_at DESC"
            )
        )
    ).all():
        acct = by_id.get(r.account_id)
        if acct is None or r.account_id not in scope:
            continue
        when = _as_dt(r.created_at)
        out.append(
            Row(
                key=f"report:{r.id}",
                kind="report",
                account=acct,
                text=str(r.text),
                title=f"{acct.name} account summary",
                meta=f"Previous deliverable · {day_month(when)}",
                when=when,
                url=None,
                grade="hypothesis" if r.kind == "hypothesis" else None,
                extra={"section": r.kind},
            )
        )
    return out


# ---------------------------------------------------------------------------------------------
# Understanding the question


@dataclass(frozen=True)
class Question:
    """What was asked, in the terms the composer uses."""

    text: str
    intent: str
    topics: tuple[str, ...]
    accounts: tuple[Acct, ...]
    window_days: int


def topics_in(value: str) -> tuple[str, ...]:
    """Topic keys named in the text (data and AI only when written as such)."""
    low = f" {value.lower()} "
    found = []
    for key, (_, terms) in TOPICS.items():
        if key == "data_and_ai":
            if re.search(r"\bdata\b|\bai\b|analytics|machine learning", low):
                found.append(key)
        elif any(t in low for t in terms[:1]) or (
            key != "salesforce" and any(t in low for t in terms)
        ):
            found.append(key)
    return tuple(found)


def mentioned_accounts(value: str, accounts: list[Acct]) -> tuple[Acct, ...]:
    """Accounts named in the question, by name or domain stem."""
    low = value.lower()
    found = []
    for a in accounts:
        stem = a.domain.split(".")[0]
        if a.name.lower() in low or (len(stem) >= 4 and re.search(rf"\b{re.escape(stem)}\b", low)):
            found.append(a)
    return tuple(found)


def understand(value: str, ws: Workspace, scope: Acct | None) -> Question:
    """Intent, topics, named accounts and time window of one question."""
    low = f" {value.lower().strip()} "
    intent = "other"
    for name, words in INTENT_WORDS:
        if any(w in low for w in words):
            intent = name
            break
    named = mentioned_accounts(value, ws.accounts)
    if scope is not None:
        named = (scope,)
    window = 7 if "this week" in low or "last week" in low else 30
    if "this quarter" in low or "90 days" in low:
        window = 90
    return Question(
        text=value.strip(),
        intent=intent,
        topics=topics_in(value),
        accounts=named,
        window_days=window,
    )


def classify(value: str) -> str:
    """The question category the admin console counts (no text is kept)."""
    low = f" {value.lower()} "
    for name, words in INTENT_WORDS:
        if any(w in low for w in words):
            return CATEGORY[name]
    return "other"


def about(row: Row, topics: tuple[str, ...]) -> bool:
    """Whether a row is about any of the topics (no topics: everything is)."""
    if not topics:
        return True
    hay = row.haystack()
    for key in topics:
        if row.lane == key:
            return True
        terms = TOPICS[key][1]
        if key == "data_and_ai":
            if re.search(r"\bdata\b|\bai\b|analytics|machine learning|databricks|snowflake", hay):
                return True
        elif any(t in hay for t in terms):
            return True
    return False


# ---------------------------------------------------------------------------------------------
# Composing


class Draft:
    """Answer text under construction; citations are row keys until numbering."""

    def __init__(self) -> None:
        self.paragraphs: list[str] = []
        self.rows: dict[str, Row] = {}
        self.items: tuple[str, list[str]] | None = None
        self.focus: Acct | None = None
        # the model's render_answer call (replica.render), validated and with row-key
        # citations; finish() numbers it for the client, which renders it as cards and tables
        self.structured: dict[str, Any] | None = None
        # the model's readable thinking summary (a ``summarized`` display, models.yaml
        # ``ask_model_settings``): shown under "How this was worked out", scrubbed with the answer
        self.thinking: str | None = None

    def cite(self, *rows: Row) -> str:
        """Number the rows as citations (reusing a row's number) and return the markers."""
        out = []
        for r in rows:
            self.rows[r.key] = r
            out.append(f"[[{r.key}]]")
        return "".join(out)

    def para(self, value: str) -> None:
        """Add one paragraph to the answer."""
        self.paragraphs.append(value)


def _recent(rows: list[Row], now: datetime, days: int) -> list[Row]:
    cutoff = now - timedelta(days=days)
    return [r for r in rows if r.when is not None and r.when >= cutoff]


def _grade_rank(grade: str | None) -> int:
    return {"confirmed": 0, "probable": 1, "hypothesis": 2, "lead": 3}.get(grade or "", 4)


def _topic_label(topics: tuple[str, ...]) -> str:
    if not topics:
        return ""
    return " and ".join(TOPICS[t][0] for t in topics)


def _join(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


PROPER = {
    "salesforce",
    "servicenow",
    "sap",
    "tableau",
    "workday",
    "mulesoft",
    "snowflake",
    "databricks",
    "ai",
    "azure",
    "aws",
    "okta",
    "epic",
    "oracle",
    "jira",
    "informatica",
}


def _lower_first(value: str) -> str:
    """Lower the first letter so a row reads inside a sentence, unless it starts with a name."""
    if not value or value.startswith("["):
        return value
    first = re.split(r"[\s,]", value, maxsplit=1)[0]
    if (
        first.lower() in PROPER
        or first.isupper()
        or (len(first) > 1 and any(c.isupper() for c in first[1:]))
    ):
        return value
    return value[:1].lower() + value[1:]


def _grade_note(r: Row) -> str:
    """' (Probable)' after a cited fact, nothing for other rows."""
    return f" ({GRADE_LABEL[r.grade]})" if r.grade in GRADE_LABEL and r.kind == "fact" else ""


def _first_call_fact(ws: Workspace, acct: Acct) -> Row | None:
    """The strongest Confirmed people or leadership fact of an account, if any."""
    options = [
        f
        for f in ws.facts
        if f.account.id == acct.id
        and f.grade == "confirmed"
        and str(f.extra.get("attr", "")).startswith(("leadership.", "people."))
    ]
    options.sort(key=lambda r: r.when or datetime.min.replace(tzinfo=UTC), reverse=True)
    return options[0] if options else None


def compose_hiring(q: Question, ws: Workspace, now: datetime, d: Draft) -> None:
    """Answer a hiring question from recent job postings matching the topics."""
    rows = [j for j in _recent(ws.jobs, now, q.window_days) if about(j, q.topics)]
    if q.accounts:
        rows = [j for j in rows if j.account in q.accounts]
    label = _topic_label(q.topics)
    noun = (
        f"{label} build roles"
        if label and "build" in q.text.lower()
        else (f"{label} roles" if label else "roles")
    )
    span = (
        "in the last 7 days"
        if q.window_days == 7
        else ("this quarter" if q.window_days == 90 else "in the last 30 days")
    )
    if not rows:
        where = f"**{q.accounts[0].name}**" if len(q.accounts) == 1 else "None of your accounts"
        verb = "has not posted" if len(q.accounts) == 1 else "posted"
        d.para(
            f"{where} {verb} {noun} {span}, as far as your workspace shows."
            if len(q.accounts) == 1
            else f"{where} {verb} {noun} {span}, as far as your workspace shows."
        )
        return
    by_acct: dict[UUID, list[Row]] = defaultdict(list)
    for j in rows:
        by_acct[j.account.id].append(j)
    ordered = sorted(by_acct.values(), key=lambda rs: (-len(rs), rs[0].account.name))
    parts = []
    for rs in ordered[:4]:
        n = len(rs)
        shown = rs[:3]
        parts.append(
            f"**{rs[0].account.name}** ({COUNT_WORD.get(n, str(n))}"
            f" role{'s' if n != 1 else ''}){d.cite(*shown)}"
        )
    count = len(ordered)
    if count == 1 and len(q.accounts) == 1:
        first = f"**{ordered[0][0].account.name}** posted {noun} {span}: "
        d.para(first + ", ".join(f"{r.text}{d.cite(r)}" for r in ordered[0][:4]) + ".")
    else:
        lead = (
            f"{NUM_WORD.get(count, str(count))} of your accounts posted"
            if count > 1
            else "One account posted"
        )
        if len(ws.accounts) == count and count > 1:
            lead = f"All {count} of your accounts posted"
        if count == 1:
            d.para(f"One account posted {noun} {span}: {parts[0]}.")
        else:
            d.para(
                f"{lead} {noun} {span}: {_join(parts)}."
                if count <= 4
                else f"{lead} {noun} {span}. The most active: {_join(parts)}."
            )
    top = ordered[0][0].account
    d.focus = top
    lead_fact = _first_call_fact(ws, top)
    if lead_fact is not None:
        lead_text = _lower_first(lead_fact.text).rstrip(".")
        sentence = (
            f"At **{top.name}**, the best first call is the {lead_text}{d.cite(lead_fact)},"
            " which is a Confirmed fact."
        )
        inferred = [
            f
            for f in ws.facts
            if f.account.id == top.id
            and f.grade == "probable"
            and str(f.extra.get("attr", "")).startswith("hiring.")
            and (f.lane in q.topics if q.topics and f.lane else about(f, q.topics))
        ]
        if inferred:
            sentence += (
                f" {inferred[0].text.rstrip('.')}{d.cite(inferred[0])}."
                " That is Probable, so ask about it rather than state it."
            )
        d.para(sentence)
    if "call" in q.text.lower() or "question" in q.text.lower():
        d.items = ("FIRST CALL QUESTIONS", first_call_questions(ws, top, q.topics)[:2])


def compose_people(q: Question, ws: Workspace, now: datetime, d: Draft) -> None:
    """Answer a people question from the people list of the scoped accounts."""
    targets = list(q.accounts) or ws.accounts
    label = _topic_label(q.topics)
    facts = [
        f
        for f in ws.facts
        if f.account in targets
        and str(f.extra.get("attr", "")).startswith(("people.", "leadership."))
        and about(f, q.topics)
    ]
    people = [p for p in ws.people if p.account in targets and about(p, q.topics)]
    people.sort(key=lambda p: (_grade_rank(p.grade), p.extra.get("priority", "Z")))
    if not facts and not people:
        where = f"at **{targets[0].name}**" if len(targets) == 1 else "in your accounts"
        d.para(
            f"Your workspace has no senior people tied to {label or 'that'} {where} yet. Not"
            " finding someone is not proof"
            f" that nobody owns it."
        )
        d.focus = targets[0] if len(targets) == 1 else None
        return
    acct = facts[0].account if facts else people[0].account
    d.focus = acct
    confirmed = [f for f in facts if f.grade == "confirmed"]
    subject = f"{label} ownership" if label else "Ownership"
    if facts:
        facts.sort(key=lambda f: _grade_rank(f.grade))
        shown = facts[:3]
        status = "confirmed" if confirmed else "not confirmed"
        names = []
        for f in shown:
            body = re.sub(r"\s*,?\s*(is closest to|may own|sits closest to).*$", "", f.text)
            names.append(f"{body}{d.cite(f)} ({GRADE_LABEL.get(f.grade or '', 'Ungraded')})")
        n = len(shown)
        d.para(
            f"{subject} at **{acct.name}** is {status}. {NUM_WORD.get(n, str(n))} senior "
            f"{'people sit' if n > 1 else 'person sits'} closest to it: {_join(names)}."
        )
        if not confirmed:
            d.para(
                "Only the first has a source that ties them to it. Confirm it on the call rather"
                " than assume."
            )
    else:
        shown_p = people[:3]
        names = [
            f"{p.text}{d.cite(p)} ({GRADE_LABEL.get(p.grade or '', 'Ungraded')})" for p in shown_p
        ]
        d.para(
            f"At **{acct.name}**, the senior people closest to {label or 'this'} are"
            f" {_join(names)}."
            " Treat anything below Confirmed as something to check on the call."
        )


def compose_changes(q: Question, ws: Workspace, now: datetime, d: Draft) -> None:
    """Answer "what changed" from the signals and findings inside the window."""
    targets = list(q.accounts) or ws.accounts
    window = max(q.window_days if q.window_days != 30 else 7, 7)
    moved: list[tuple[Acct, list[Row]]] = []
    quiet_refreshed: list[Acct] = []
    for a in targets:
        fresh = [s for s in _recent(ws.signals, now, window) if s.account.id == a.id]
        if fresh:
            moved.append((a, fresh))
        elif (
            a.last_refresh
            and a.last_refresh >= now - timedelta(days=max(window, 10))
            and a.refreshes > 1
        ):
            quiet_refreshed.append(a)
    if not moved and not quiet_refreshed:
        d.para(
            "Nothing new has been recorded at your"
            f" {'account' if len(targets) == 1 else 'accounts'} in the last {window} days."
        )
        d.focus = targets[0] if len(targets) == 1 else None
        return
    sentences = []
    head = (
        f"Since your last visit, {COUNT_WORD.get(len(moved), str(len(moved)))} "
        f"account{'s' if len(moved) != 1 else ''} moved."
        if len(targets) > 1
        else ""
    )
    for a, fresh in moved[:4]:
        items = [f"{_lower_first(s.text).rstrip('.')}{d.cite(s)}" for s in fresh[:3]]
        sentences.append(f"**{a.name}**: {_join(items)}.")
    for a in quiet_refreshed[:3]:
        sentences.append(f"**{a.name}** was refreshed with no material change.")
    rest = len(targets) - len(moved) - len(quiet_refreshed)
    if len(targets) > 1 and rest > 0:
        sentences.append(f"Nothing new at the other {COUNT_WORD.get(rest, str(rest))}.")
    d.para(" ".join(([head] if head else []) + sentences))
    d.focus = moved[0][0] if moved else quiet_refreshed[0]


def first_call_questions(ws: Workspace, acct: Acct, topics: tuple[str, ...] = ()) -> list[str]:
    """Stored first call questions for an account, else questions drawn from its findings."""
    stored = ws.questions.get(acct.id) or []
    if stored:
        return [f"{i + 1}. {q}" for i, q in enumerate(stored[:3])]
    out = []
    facts = [f for f in ws.facts if f.account.id == acct.id]
    if any(str(f.extra.get("attr", "")).startswith("leadership.") for f in facts):
        out.append("What changed in your technology organization this quarter?")
    lanes = acct.lanes + [LANE_LABEL.get(f.lane or "", "") for f in facts if f.lane]
    lanes = [x for x in dict.fromkeys(lanes) if x]
    if topics:
        lanes = [TOPICS[t][0] for t in topics] + lanes
    if lanes:
        out.append(f"How are you sequencing the {lanes[0]} work this year?")
    if any(f.kind == "fact" and str(f.extra.get("attr", "")).startswith("hiring.") for f in facts):
        out.append("Where do the new roles sit in that plan?")
    out.append(
        f"Who else should be in the room for a {lanes[0] if lanes else 'platform'} conversation?"
    )
    return [f"{i + 1}. {q}" for i, q in enumerate(list(dict.fromkeys(out))[:3])]


def compose_first_call(q: Question, ws: Workspace, now: datetime, d: Draft) -> None:
    """Suggest a first call from the account's strongest recent findings and people."""
    if not q.accounts:
        best = max(
            ws.accounts,
            key=lambda a: sum(
                1 for f in ws.facts if f.account.id == a.id and f.grade == "confirmed"
            ),
            default=None,
        )
        if best is None:
            return
        acct = best
    else:
        acct = q.accounts[0]
    d.focus = acct
    confirmed = sorted(
        [f for f in ws.facts if f.account.id == acct.id and f.grade == "confirmed"],
        key=lambda f: (not f.extra.get("key_finding"), -(f.when.timestamp() if f.when else 0)),
    )
    if confirmed:
        d.para(
            f"Here is a starting set for **{acct.name}**, built only from what is"
            f" Confirmed{d.cite(*confirmed[:2])}."
            " Do not state anything graded Probable or Hypothesis as fact."
        )
    else:
        d.para(
            f"Nothing about **{acct.name}** is Confirmed yet, so these questions only ask. Do not"
            " state anything"
            " graded Probable or Hypothesis as fact."
        )
    d.items = ("FIRST CALL QUESTIONS", first_call_questions(ws, acct, q.topics))


def compose_technology(q: Question, ws: Workspace, now: datetime, d: Draft) -> None:
    """Answer a technology question from the tech stack and graded findings."""
    targets = list(q.accounts) or ws.accounts
    rows = [t for t in ws.tech if t.account in targets and about(t, q.topics)]
    facts = [
        f
        for f in ws.facts
        if f.account in targets
        and str(f.extra.get("attr", "")).startswith("tech.")
        and about(f, q.topics)
    ]
    if not rows and not facts:
        label = _topic_label(q.topics) or "that technology"
        d.para(
            f"Your workspace has no detection of {label}"
            f" {'at **' + targets[0].name + '**' if len(targets) == 1 else 'in your accounts'}."
            " That means unknown, not absent."
        )
        d.focus = targets[0] if len(targets) == 1 else None
        return
    by_acct: dict[UUID, list[Row]] = defaultdict(list)
    for r in facts + rows:
        by_acct[r.account.id].append(r)
    sentences = []
    for rs in sorted(by_acct.values(), key=lambda rs: -len(rs))[:4]:
        a = rs[0].account
        names = list(dict.fromkeys(r.text if r.kind == "technology" else r.text for r in rs))[:4]
        cites = d.cite(*rs[: len(names)])
        sentences.append(f"**{a.name}**: {_join(names)}{cites}.")
    d.para(" ".join(sentences))
    d.focus = next(iter(by_acct.values()))[0].account


def compose_other(q: Question, ws: Workspace, now: datetime, d: Draft) -> None:
    """Answer anything else by matching the question's words against stored rows."""
    words = [
        w
        for w in re.findall(r"[a-z0-9]{4,}", q.text.lower())
        if w
        not in {
            "what",
            "which",
            "about",
            "have",
            "does",
            "with",
            "from",
            "that",
            "this",
            "there",
            "their",
            "they",
            "accounts",
            "account",
            "workspace",
            "tell",
            "know",
            "show",
            "give",
            "anything",
            "please",
        }
    ]
    pool = ws.facts + ws.jobs + ws.signals + ws.files + ws.reports + ws.tracker
    if q.accounts:
        pool = [r for r in pool if r.account in q.accounts]
    scored = []
    for r in pool:
        hay = r.haystack()
        score = sum(1 for w in words if w in hay)
        if score:
            scored.append(
                (score, -_grade_rank(r.grade), r.when or datetime.min.replace(tzinfo=UTC), r)
            )
    scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
    if not scored and q.accounts:
        a = q.accounts[0]
        keys = sorted(
            [f for f in ws.facts if f.account.id == a.id and f.extra.get("key_finding")],
            key=lambda f: _grade_rank(f.grade),
        )
        if keys:
            d.focus = a
            d.para(
                f"Here is what your workspace holds on **{a.name}**: "
                + " ".join(
                    f"{f.text.rstrip('.')}{d.cite(f)} ({GRADE_LABEL.get(f.grade or '', '')})."
                    for f in keys[:3]
                )
            )
            return
    if not scored:
        d.para(NO_ANSWER.format(n=len(ws.accounts)))
        return
    top = [r for *_, r in scored[:3]]
    d.focus = top[0].account
    # Generic questions can still resolve to job postings (for example, "what do you
    # have on this account?"). Keep those answers structured and complete instead of reducing
    # the retrieved rows to the three-item narrative fallback below.
    if top[0].kind == "job":
        best_score = scored[0][0]
        job_rows = [
            r
            for score, *_rest, r in scored
            if r.kind == "job" and r.account.id == top[0].account.id and score == best_score
        ][:400]
        if job_rows:
            account = top[0].account.name
            table_rows = []
            for r in job_rows:
                table_rows.append(
                    [
                        r.account.name,
                        r.text.removesuffix(" (closed)"),
                        str(r.extra.get("location") or "Not listed"),
                        str(r.extra.get("status") or "Not listed").replace("_", " ").title(),
                        day_month(r.when) if r.when else "Not listed",
                        "Workspace posting" + d.cite(r),
                    ]
                )
            d.para(
                f"Your workspace contains {len(job_rows)} relevant job posting"
                f"{'s' if len(job_rows) != 1 else ''} at **{account}**."
                + "".join(d.cite(r) for r in job_rows)
            )
            d.structured = {
                "headline": (
                    f"Your workspace contains {len(job_rows)} relevant job posting"
                    f"{'s' if len(job_rows) != 1 else ''} at {account}."
                ),
                "stats": [{"label": "Relevant postings", "value": str(len(job_rows))}],
                "sections": [
                    {
                        "kind": "table",
                        "title": "Job postings",
                        "columns": ["Account", "Role", "Location", "Status", "Posted", "Source"],
                        "rows": table_rows,
                    }
                ],
            }
            return
    d.para(
        "Here is what your workspace holds on that: "
        + " ".join(
            f"At **{r.account.name}**, {_lower_first(r.text).rstrip('.')}{d.cite(r)}"
            f"{_grade_note(r)}."
            for r in top
        )
    )


COMPOSERS = {
    "hiring": compose_hiring,
    "people": compose_people,
    "changes": compose_changes,
    "first_call": compose_first_call,
    "technology": compose_technology,
    "other": compose_other,
}


# ---------------------------------------------------------------------------------------------
# Output


# a row key is ``kind:<uuid>``; a retrieved passage's key carries its sequence number too
# (``passage:<uuid>:<seq>``, documents/retrieve.py). Every marker pattern must accept that
# tail, else a passage citation leaks into the rendered answer as a raw [[…]] marker and the
# row earns no Sources entry (fixed 2026-10-17 — the structured-answer post-processor).
_ROW_KEY = r"[a-z_]+:[0-9a-f-]{36}(?::\d+)?"
_KEYS_IN = re.compile(r"\[\[(" + _ROW_KEY + r")\]\]")
_MARK = re.compile(r"\[\[(" + _ROW_KEY + r")\]\]|\[(\d{1,3})\]|\*\*(.+?)\*\*")
# any [[…]] that survived ``to_parts`` (a marker that matched no numbered row) never reaches
# the client: a streamed or stored answer shows citations, never raw row markers
_LEFTOVER = re.compile(r"\s*\[\[[^\]\n]{0,200}\]\]")


def to_parts(value: str, numbering: dict[str, int]) -> list[dict[str, str]]:
    """Split answer text into text, bold and cite parts. Row-key citations become numbers."""
    parts: list[dict[str, str]] = []
    pos = 0
    for m in _MARK.finditer(value):
        if m.start() > pos:
            parts.append({"t": "text", "v": _LEFTOVER.sub("", value[pos : m.start()])})
        if m.group(1):
            parts.append({"t": "cite", "v": str(numbering[m.group(1)])})
        elif m.group(2):
            parts.append({"t": "cite", "v": m.group(2)})
        else:
            # a citation swallowed by a bold match is at least not shown as a raw marker
            parts.append({"t": "bold", "v": _LEFTOVER.sub("", m.group(3))})
        pos = m.end()
    if pos < len(value):
        parts.append({"t": "text", "v": _LEFTOVER.sub("", value[pos:])})
    return [p for p in parts if p["v"]]


_PIECE_END = re.compile(r"[.!?](?:\s*\[\[" + _ROW_KEY + r"\]\])*\s+(?=\S)")


def pieces(paragraph: str) -> list[str]:
    """A drafted paragraph split into sentences, each with the citations that follow it (the
    pieces an answer streams in)."""
    out, pos = [], 0
    for m in _PIECE_END.finditer(paragraph):
        out.append(paragraph[pos : m.end()])
        pos = m.end()
    if pos < len(paragraph):
        out.append(paragraph[pos:])
    return [x for x in out if x.strip()]


def number_marks(text: str, numbering: dict[str, int]) -> list[dict[str, str]]:
    """One streamed piece as answer parts, numbering new citations in order of appearance (the
    same numbers ``finish`` gives the whole answer)."""
    for key in _KEYS_IN.findall(text):
        numbering.setdefault(key, len(numbering) + 1)
    return to_parts(text, numbering)


def structured_out(payload: Mapping[str, Any], numbering: dict[str, int]) -> dict[str, Any]:
    """A validated render_answer payload for the client: every row-key marker in its strings
    becomes the citation's number, written ``[n]`` — anything else is wiped."""

    def swap(value: Any) -> Any:
        if isinstance(value, str):
            value = _KEYS_IN.sub(lambda m: f"[{numbering[m.group(1)]}]", value)
            return _LEFTOVER.sub("", value)
        if isinstance(value, list):
            return [swap(v) for v in value]
        if isinstance(value, dict):
            return {k: swap(v) for k, v in value.items()}
        return value

    out: dict[str, Any] = swap(dict(payload))
    return out


def plain_text(paragraphs: list[list[dict[str, str]]]) -> str:
    """The answer as plain text with [n] marks (stored as the turn's assistant content)."""
    out = []
    for para in paragraphs:
        out.append("".join(f"[{p['v']}]" if p["t"] == "cite" else p["v"] for p in para))
    return "\n\n".join(out)


_MATCH_OPS = frozenset({"eq", "contains", "icontains"})


def _sheet_forms(key: str) -> list[Any]:
    """Every form of a deep-dive sheet a link may open: the live sheet and, for a table the
    files print, the file-shaped one (``sheets.parse`` picks one by the account's delivery)."""
    from account_intelligence.replica import sheets as SH

    forms = [SH.SHEETS[key]] if key in SH.SHEETS else []
    if key in SH.FILE_TABLES:
        forms.append(SH.file_sheet(key))
    return forms


def sheet_column(key: str, column: Any, *, by_key: bool = False) -> tuple[str, str] | None:
    """The sheet column (key, label) a table column maps to: a matchable column of the sheet
    whose label is the table column's header, case folded (a workbook's "Location" is the Jobs
    sheet's ``location``; ``by_key``: a research table's column is also the sheet's by its key).
    The column must be matchable in every form of the sheet, so the link opens whichever form the
    account shows; None otherwise."""
    forms = _sheet_forms(key)
    header = str(column.raw).strip().casefold()
    for form in forms:
        for c in form.cols:
            if not c.match or (
                c.label.strip().casefold() != header and not (by_key and c.key == column.name)
            ):
                continue
            if all(any(o.key == c.key and o.match for o in other.cols) for other in forms):
                return c.key, c.label
    return None


def carry_table_matches(rows: Any, stored: Mapping[str, tuple[Any, Any, Any]]) -> None:
    """Copy the filter of each cited sheet table's last stored query onto its citation
    (``extra["sheet"]``): one equality or contains test on a column that maps to a matchable
    sheet column (``sheet_column``) opens the sheet narrowed to those rows; any other query
    (several conditions, another operator, a radius) opens the whole sheet."""
    last: dict[str, Any] = {}
    for table, query, _ in stored.values():
        last[f"table:{table.id}"] = (table, query)
    for row in rows:
        link = row.extra.get("sheet")
        if not isinstance(link, dict) or row.key not in last:
            continue
        table, query = last[row.key]
        for k in ("match_col", "match", "match_label"):
            link.pop(k, None)
        if len(query.where) != 1 or getattr(query, "radius_miles", None) is not None:
            continue
        if getattr(query, "distinct_on", None):
            continue  # the answer counted distinct rows; the sheet would list every copy
        where = query.where[0]
        if getattr(where, "also", ()):
            continue  # the answer also matched other spellings; the sheet matches one
        value = str(where.value).strip() if isinstance(where.value, str) else ""
        if where.op not in _MATCH_OPS or not value or len(value) > 200 or "\x00" in value:
            continue
        if not _client_safe(value):
            continue  # the value is printed in the source's line; the answer gate must pass it
        found = sheet_column(str(link["key"]), where.column, by_key=table.view is not None)
        if found is not None:
            link.update({"match_col": found[0], "match": value, "match_label": found[1]})


def _client_safe(value: str) -> bool:
    """Whether a filter value may be printed in a source's line: the client-text guard passes it
    unchanged (no provider or tool name, person name or provider domain) and it carries no dash
    the answer gate refuses."""
    from account_intelligence.replica.sheets import _GATE_DASHES
    from agents.naming import scrub_client_text

    shown, withheld = scrub_client_text(value)
    return not withheld and shown == value and not _GATE_DASHES.search(value)


def _sheet_title(key: str) -> str:
    """The title of a deep-dive sheet as a source names it ("Job postings sheet")."""
    forms = _sheet_forms(key)
    return f"{forms[0].title} sheet" if forms else "Deep-dive sheet"


def source_out(n: int, r: Row) -> dict[str, Any]:
    """One numbered source as the answer's SOURCES list shows it: the link opens the original
    page and reads "6th / LinkedIn" (``source_label``; a data provider's own page has none). A
    cited table that is a deep-dive sheet names the sheet and carries ``sheet`` (the browser
    opens the sheet, matched by the answer's filter); its ``url`` stays None."""
    from account_intelligence.replica.source_label import source_link

    link = source_link(r.url)
    sheet = r.extra.get("sheet")
    if isinstance(sheet, dict) and sheet.get("key"):
        matched = bool(sheet.get("match_col") and sheet.get("match"))
        return {
            "n": n,
            "title": _sheet_title(str(sheet["key"])),
            "meta": (
                f"Filtered: {sheet.get('match_label') or sheet['match_col']} contains"
                f" {sheet['match']}"
                if matched
                else r.meta
            ),
            "date": r.when.date().isoformat() if r.when else None,
            "url": None,
            "label": None,
            "kind": r.kind,
            "account_domain": r.account.domain,
            "account_name": r.account.name,
            "sheet": {
                "key": str(sheet["key"]),
                "match_col": sheet["match_col"] if matched else None,
                "match": sheet["match"] if matched else None,
            },
        }
    return {
        "n": n,
        "title": r.title,
        "meta": r.meta,
        "date": r.when.date().isoformat() if r.when else None,
        "url": link.href if link else None,
        "label": link.label if link else None,
        "kind": r.kind,
        "account_domain": r.account.domain,
        "account_name": r.account.name,
        # every source carries the key, as the thread's stored answer reads back (AnswerSource)
        "sheet": None,
    }


def offer_for(
    focus: Acct | None, cited: list[Row], answered: bool, now: datetime, scope_single: Acct | None
) -> dict[str, Any] | None:
    """Offer a confirmed web refresh when the newest cited row is older than the account's
    cadence, or when the workspace could not answer."""
    acct = focus or scope_single
    if not answered:
        return {
            "account_domain": acct.domain if acct else None,
            "account_name": acct.name if acct else None,
            "refreshed_days_ago": acct.refreshed_days(now) if acct else None,
        }
    if acct is None:
        return None
    newest = max((r.when for r in cited if r.account.id == acct.id and r.when), default=None)
    limit = CADENCE_DAYS.get(acct.cadence or "", 7)
    if newest is None and any(r.account.id == acct.id and r.kind == "file" for r in cited):
        # the uploaded files' passages and tables carry no date of their own: an answer resting
        # on them is as fresh as the account's last refresh, not "undated" (staging 2026-11-03:
        # three of six answers from one zip offered the web, three did not, at random)
        days = acct.refreshed_days(now)
        if days is not None and days <= limit:
            return None
    if newest is None or (now - newest).days > limit:
        return {
            "account_domain": acct.domain,
            "account_name": acct.name,
            "refreshed_days_ago": acct.refreshed_days(now),
        }
    return None


def suggestions(ws: Workspace, now: datetime, limit: int = 5) -> list[str]:
    """Questions the workspace can answer well right now, drawn from its rows."""
    out: list[str] = []
    recent_jobs = _recent(ws.jobs, now, 30)
    lane_accounts: dict[str, set[UUID]] = defaultdict(set)
    for j in recent_jobs:
        for key in topics_in(f"{j.text} {j.extra.get('lanes', '')}"):
            lane_accounts[key].add(j.account.id)
    for key, _accts in sorted(lane_accounts.items(), key=lambda kv: -len(kv[1])):
        if key != "data_and_ai":
            out.append(f"Which of my accounts posted {TOPICS[key][0]} build roles this month?")
            break
    owners = [
        f
        for f in ws.facts
        if str(f.extra.get("attr", "")).startswith("people.") and f.lane in TOPICS
    ]
    if owners:
        f = owners[0]
        out.append(
            f"Who owns {LANE_LABEL.get(f.lane or '', TOPICS[f.lane or 'salesforce'][0])} at"
            f" {f.account.name}?"
        )
    if _recent(ws.signals, now, 7):
        out.append("What changed this week?")
    # the account the workspace knows most about: confirmed facts first, then any research row;
    # an account with no research at all is never suggested (2026-11-03: the tenant's own
    # company, first by name and with nothing held, was offered as "What do we know about 6th?")
    held = _research_counts(ws)
    best = max(
        (a for a in ws.accounts if held.get(a.id)),
        key=lambda a: (
            sum(1 for f in ws.facts if f.account.id == a.id and f.grade == "confirmed"),
            held[a.id],
        ),
        default=None,
    )
    if best is not None and any(f.account.id == best.id for f in ws.facts):
        out.append(f"First call questions for {best.name}")
    if len(lane_accounts.get("data_and_ai", set())) >= 2:
        out.append("Which accounts hired in data and AI?")
    if not out and best is not None:
        out.append(f"What do we know about {best.name}?")
    return list(dict.fromkeys(out))[:limit]


def _research_counts(ws: Workspace) -> dict[UUID, int]:
    """How many research rows (facts, postings, people, signals, stack, files) each account
    holds."""
    counts: dict[UUID, int] = defaultdict(int)
    for rows in (ws.facts, ws.jobs, ws.people, ws.signals, ws.tech, ws.files):
        for r in rows:
            counts[r.account.id] += 1
    return counts


def followups(ws: Workspace, q: Question, focus: Acct | None, now: datetime) -> list[str]:
    """Two related questions, never the one just asked."""
    pool = suggestions(ws, now, limit=8)
    if focus is not None:
        pool.insert(0, f"First call questions for {focus.name}")
    asked = re.sub(r"\W+", " ", q.text.lower()).strip()
    picked = []
    for s in dict.fromkeys(pool):
        norm = re.sub(r"\W+", " ", s.lower()).strip()
        if norm == asked or (
            q.intent != "other" and classify(s) == CATEGORY[q.intent] and len(pool) > 3
        ):
            continue
        picked.append(s)
        if len(picked) == 2:
            break
    return picked


def finish(
    d: Draft, q: Question, ws: Workspace, now: datetime, scope: Acct | None
) -> dict[str, Any]:
    """Number the citations, build the source list, the used summary, the offer and follow-ups."""
    numbering: dict[str, int] = {}
    for para in d.paragraphs:
        for key in _KEYS_IN.findall(para):
            numbering.setdefault(key, len(numbering) + 1)
    paragraphs = [to_parts(p, numbering) for p in d.paragraphs]
    cited = [d.rows[k] for k in numbering]
    # the model's own knowledge and a web result are cited like any source, but neither is an
    # answer from the workspace
    held = [r for r in cited if not r.extra.get("knowledge") and not r.extra.get("web")]
    answered = bool(held)
    focus = d.focus or (held[0].account if held else None)
    return {
        "paragraphs": paragraphs,
        "structured": structured_out(d.structured, numbering) if d.structured else None,
        "items": {"title": d.items[0], "items": d.items[1]} if d.items and d.items[1] else None,
        "sources": [source_out(numbering[r.key], r) for r in cited],
        "used": {
            "workspace": "used" if answered else "not_used",
            "web": "used" if any(r.extra.get("web") for r in cited) else "not_used",
        },
        "offer": offer_for(focus, held, answered, now, scope),
        "followups": followups(ws, q, focus, now),
        "account_domain": focus.domain if focus else None,
        "account_name": focus.name if focus else None,
        "answered": answered,
        "thinking": _thinking_out(d.thinking),
    }


# the longest thinking summary an answer carries (the model's own summary, already short)
THINKING_MAX_CHARS = 6000
_ANY_CITE = re.compile(r"\[\[[^\]]+\]\]|\[(?:R\d{1,3}|W\d{1,2}|K)\]")


def _thinking_out(text: str | None) -> str | None:
    """The thinking summary as the client shows it: citation markers removed (they number
    nothing here), whitespace kept to paragraphs, bounded; None when there is none."""
    if not text:
        return None
    clean = _ANY_CITE.sub("", text)
    clean = "\n\n".join(" ".join(p.split()) for p in clean.split("\n\n") if p.strip())
    clean = re.sub(r" +([,;.:!?])", r"\1", clean)  # a removed citation leaves no gap
    if not clean:
        return None
    if len(clean) > THINKING_MAX_CHARS:
        clean = clean[: THINKING_MAX_CHARS - 1].rstrip() + "…"
    return clean


def draft_local(
    question: str, ws: Workspace, now: datetime, scope: Acct | None = None
) -> tuple[Question, Draft]:
    """Understand one question and draft its answer from the workspace rows, in code."""
    q = understand(question, ws, scope)
    d = Draft()
    if not ws.accounts:
        d.para("Your workspace has no accounts yet. Add an account and its research appears here.")
        return q, d
    COMPOSERS[q.intent](q, ws, now, d)
    if not d.rows and q.intent != "other":
        fallback = Draft()
        compose_other(q, ws, now, fallback)
        if fallback.rows:
            d = fallback
        elif not d.paragraphs:
            d.para(NO_ANSWER.format(n=len(ws.accounts)))
    return q, d


def compose_local(
    question: str, ws: Workspace, now: datetime, scope: Acct | None = None
) -> dict[str, Any]:
    """Answer one question from the workspace rows, in code."""
    q, d = draft_local(question, ws, now, scope)
    return finish(d, q, ws, now, scope)


def scope_account(ws: Workspace, account_id: UUID | None) -> Acct | None:
    """The account the question is scoped to, if any (None means all accounts)."""
    return next((a for a in ws.accounts if a.id == account_id), None) if account_id else None
