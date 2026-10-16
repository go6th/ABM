"""Workspace answers for Ask: retrieve stored rows, compose an answer that cites them, and say
what the answer used.

Answers come only from what the workspace holds: stored facts, job postings, people, signals,
technology, and the caller's own matched mail and drive items. Nothing here calls the web or a
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
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.replica.settings import CADENCE_DAYS

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
        extra = " ".join(map(str, self.extra.values()))
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
    mail: list[Row]
    questions: dict[UUID, list[str]]
    mail_connected: bool
    # Internal facts from the client's uploaded files (labelled "From your files"), the research
    # tracker of each account's latest run, and the text of previous deliverables
    files: list[Row] = field(default_factory=list)
    # passages of the client's own documents retrieved for THIS question (hybrid search,
    # documents.retrieve; filled by the Ask route once the question is known)
    passages: list[Row] = field(default_factory=list)
    # the best-matching uploaded file, whole, when it fits the budget (take_whole_file)
    whole_files: list[Row] = field(default_factory=list)
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
    """Read the rows an answer may cite (tenant scoped by RLS; mail and drive by owner).
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
                " FROM accounts a ORDER BY coalesce(a.name, a.domain)"
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
                "SELECT f.id, f.account_id, f.attribute_key, coalesce(f.value_text,"
                " f.value_json->>'text') AS v,"
                " f.product_grade, f.lane, f.flags, f.created_at, f.visibility,"
                " e.url, e.evidence_date, s.title AS source_title"
                " FROM facts f"
                " LEFT JOIN LATERAL (SELECT url, evidence_date, source_id FROM fact_evidence x"
                "   WHERE x.fact_id = f.id ORDER BY evidence_date DESC NULLS LAST LIMIT 1) e ON"
                " true"
                " LEFT JOIN sources s ON s.id = e.source_id"
                " WHERE f.status = 'current' AND f.superseded_at IS NULL AND f.visibility ="
                " 'public'"
                " ORDER BY f.created_at DESC LIMIT 2000"
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
                "SELECT id, account_id, title, url, posted_at, first_seen, lanes, status,"
                " location, created_at"
                " FROM job_postings"
                + ("" if include_closed else " WHERE status <> 'closed'")
                + " ORDER BY coalesce(posted_at, first_seen) DESC NULLS LAST"
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
                "SELECT p.id, p.account_id, coalesce(p.name, '[Name]') AS name, p.title,"
                " p.priority,"
                " p.classification_json, p.created_at, s.title AS source_title, s.url AS source_url"
                " FROM people p LEFT JOIN sources s ON s.account_id = p.account_id AND"
                " s.source_code = p.source_ref"
                " WHERE NOT p.excluded AND p.merged_into IS NULL AND p.erased_at IS NULL"
                " AND coalesce(p.internal_only, false) = false LIMIT 3000"
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
                "SELECT t.id, t.account_id, t.technology, t.cross_check_result, t.created_at,"
                " s.url, s.title AS source_title"
                " FROM tech_detections t LEFT JOIN sources s ON s.account_id = t.account_id AND"
                " s.source_code = t.source_ref"
                " LIMIT 2000"
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

    mail: list[Row] = []
    connected = bool(
        await session.scalar(
            text(
                "SELECT count(*) FROM connector_connections WHERE owner_user_id = :u AND status ="
                " 'active'"
            ),
            {"u": user_id},
        )
    )
    for r in (
        await session.execute(
            text(
                "SELECT id, account_id, item_kind, title, snippet, item_date, location FROM"
                " connector_items"
                " WHERE owner_user_id = :u AND state = 'matched' AND account_id IS NOT NULL LIMIT"
                " 500"
            ),
            {"u": user_id},
        )
    ).all():
        acct = by_id.get(r.account_id)
        if acct is None or r.account_id not in scope:
            continue
        when = _as_dt(r.item_date)
        mail.append(
            Row(
                key=f"mail_drive:{r.id}",
                kind="mail_drive",
                account=acct,
                text=str(r.snippet or r.title or ""),
                title=str(r.title or "Mail and drive item"),
                meta=f"From your mail and drive · {day_month(when)}",
                when=when,
                url=None,
                grade="internal",
                extra={"location": r.location or ""},
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

    return Workspace(
        accounts=accounts,
        facts=facts,
        jobs=jobs,
        people=people,
        signals=signals,
        tech=tech,
        mail=mail,
        questions=questions,
        mail_connected=connected,
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
                title=f"From your files: {r.document}",
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
                title=f"From your files: {p.document}",
                meta=f"Internal · {p.cite}" if p.cite else "Internal · from your files",
                when=None,
                url=None,
                grade="internal",
                extra={"quote": p.content[:300], "passage": True},
            )
        )
    return out


async def take_whole_file(session: Any, ws: Workspace, max_chars: int) -> None:
    """Give the model the whole of the file the question is about (owner decision 2026-10-17:
    an answer over a spreadsheet must count every row, as a file attached to a chat does).

    The file is the one the best-ranked retrieved passage came from. Its passages, in order,
    become one ``whole_file`` row (cited as one source) and leave ``ws.passages``; a file above
    ``max_chars`` stays as retrieved passages only, so a question never ships more than the
    budget to the model. Same tenant scope as retrieval (row level security)."""
    if not ws.passages or max_chars <= 0:
        return
    best = ws.passages[0].key  # passage:<document id>:<seq>
    parts = best.split(":")
    if len(parts) != 3:
        return
    try:
        doc_id = UUID(parts[1])
    except ValueError:
        return
    head = (
        await session.execute(
            text(
                "SELECT d.name, d.account_id, count(p.id) AS n, coalesce(sum(length(p.content)), 0)"
                " AS chars FROM documents d JOIN document_passages p ON p.document_id = d.id"
                " WHERE d.id = :d GROUP BY d.id, d.name, d.account_id"
            ),
            {"d": doc_id},
        )
    ).first()
    if head is None or not head.n or int(head.chars) > max_chars:
        return
    acct = next((a for a in ws.accounts if a.id == head.account_id), None)
    if acct is None:
        return
    contents = (
        await session.execute(
            text("SELECT content FROM document_passages WHERE document_id = :d ORDER BY seq"),
            {"d": doc_id},
        )
    ).scalars()
    body = "\n".join(str(c).strip() for c in contents if c)
    if not body.strip():
        return
    name = str(head.name or "an uploaded file")
    ws.whole_files = [
        Row(
            key=f"whole:{doc_id}",
            kind="file",
            account=acct,
            text=body,
            title=f"From your files: {name} (the whole file)",
            meta=f"Internal · {int(head.n)} passages, read whole",
            when=None,
            url=None,
            grade="internal",
            extra={"whole_file": True, "document": name, "lines": body.count("\n") + 1},
        )
    ]
    prefix = f"passage:{doc_id}:"
    ws.passages = [r for r in ws.passages if not r.key.startswith(prefix)]


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
    pool = ws.facts + ws.jobs + ws.signals + ws.mail + ws.files + ws.reports + ws.tracker
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


_MARK = re.compile(r"\[\[([a-z_]+:[0-9a-f-]{36})\]\]|\[(\d{1,3})\]|\*\*(.+?)\*\*")


def to_parts(value: str, numbering: dict[str, int]) -> list[dict[str, str]]:
    """Split answer text into text, bold and cite parts. Row-key citations become numbers."""
    parts: list[dict[str, str]] = []
    pos = 0
    for m in _MARK.finditer(value):
        if m.start() > pos:
            parts.append({"t": "text", "v": value[pos : m.start()]})
        if m.group(1):
            parts.append({"t": "cite", "v": str(numbering[m.group(1)])})
        elif m.group(2):
            parts.append({"t": "cite", "v": m.group(2)})
        else:
            parts.append({"t": "bold", "v": m.group(3)})
        pos = m.end()
    if pos < len(value):
        parts.append({"t": "text", "v": value[pos:]})
    return [p for p in parts if p["v"]]


_PIECE_END = re.compile(r"[.!?](?:\s*\[\[[a-z_]+:[0-9a-f-]{36}\]\])*\s+(?=\S)")


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
    for key in re.findall(r"\[\[([a-z_]+:[0-9a-f-]{36})\]\]", text):
        numbering.setdefault(key, len(numbering) + 1)
    return to_parts(text, numbering)


def plain_text(paragraphs: list[list[dict[str, str]]]) -> str:
    """The answer as plain text with [n] marks (stored as the turn's assistant content)."""
    out = []
    for para in paragraphs:
        out.append("".join(f"[{p['v']}]" if p["t"] == "cite" else p["v"] for p in para))
    return "\n\n".join(out)


def source_out(n: int, r: Row) -> dict[str, Any]:
    """One numbered source as the answer's SOURCES list shows it."""
    return {
        "n": n,
        "title": r.title,
        "meta": r.meta,
        "date": r.when.date().isoformat() if r.when else None,
        "url": r.url,
        "kind": r.kind,
        "account_domain": r.account.domain,
        "account_name": r.account.name,
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
    best = max(
        ws.accounts,
        key=lambda a: sum(1 for f in ws.facts if f.account.id == a.id and f.grade == "confirmed"),
        default=None,
    )
    if best is not None and any(f.account.id == best.id for f in ws.facts):
        out.append(f"First call questions for {best.name}")
    if len(lane_accounts.get("data_and_ai", set())) >= 2:
        out.append("Which accounts hired in data and AI?")
    if not out and ws.accounts:
        out.append(f"What do we know about {ws.accounts[0].name}?")
    return list(dict.fromkeys(out))[:limit]


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
        for key in re.findall(r"\[\[([a-z_]+:[0-9a-f-]{36})\]\]", para):
            numbering.setdefault(key, len(numbering) + 1)
    paragraphs = [to_parts(p, numbering) for p in d.paragraphs]
    cited = [d.rows[k] for k in numbering]
    answered = bool(cited)
    focus = d.focus or (cited[0].account if cited else None)
    used_mail = any(r.kind == "mail_drive" for r in cited)
    return {
        "paragraphs": paragraphs,
        "items": {"title": d.items[0], "items": d.items[1]} if d.items and d.items[1] else None,
        "sources": [source_out(numbering[r.key], r) for r in cited],
        "used": {
            "workspace": "used" if answered else "not_used",
            "web": "not_used",
            "mail_drive": "used"
            if used_mail
            else ("not_used" if ws.mail_connected else "not_connected"),
        },
        "offer": offer_for(focus, cited, answered, now, scope),
        "followups": followups(ws, q, focus, now),
        "account_domain": focus.domain if focus else None,
        "account_name": focus.name if focus else None,
        "answered": answered,
    }


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
