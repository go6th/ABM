"""Read models for the Accounts list and the Account workspace, as the design shows them.

Everything is read inside the caller's tenant transaction (row level security), so another
client's account is simply not found.

* Account state (the row's sub-line): a job running or queued is "researching", a job waiting
  for credits is "waiting_credit_limit", a failed latest job is "failed"; otherwise an account
  refreshed more than once has "new_signals" (signals detected since the previous refresh) or
  "no_change", one researched once is "first_scan", and one never researched is "not_researched".
* The workspace: the one-minute narrative with the sources each sentence rests on, key findings,
  what changed in the latest refresh (only after a refresh; never on a first scan), the deep-dive
  counts (Coverage as items Done out of all tracker items) and the deliverable files.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.db.session import session_tenant
from account_intelligence.replica.answers import LANE_LABEL, day_month
from account_intelligence.replica.current import (
    NEWEST_DETECTION,
    NEWEST_FACT,
    NEWEST_PERSON,
    NEWEST_POSTING,
)
from account_intelligence.replica.settings import CADENCE_LABEL
from people.config import internal_people_sql

DELIVERABLE_LABEL = {
    "summary": "Account summary",
    "people": "People list",
    "jobs": "Job postings",
    "tech": "Tech stack and teams",
    "evidence": "Evidence",
}
DELIVERABLE_ORDER = ["summary", "people", "jobs", "tech", "evidence"]
TYPE_LABEL = {"docx": "Word", "xlsx": "Excel", "zip": "Zip"}
GRADE_RANK = {"confirmed": 0, "probable": 1, "hypothesis": 2, "lead": 3, "internal": 4}


def _original_page(url: Any) -> str | None:
    """The original page a source opens, never a data provider's own page (``source_label``)."""
    from account_intelligence.replica.source_label import source_link

    link = source_link(url)
    return link.href if link else None


def file_type(filename: str) -> str:
    """'Word', 'Excel' or 'Zip' from the file's extension."""
    return TYPE_LABEL.get(filename.rsplit(".", 1)[-1], "File")


def size_label(n: int) -> str:
    """'240 KB', '1.2 MB'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} MB"
    return f"{max(1, round(n / 1000))} KB"


def relative(at: datetime | None, now: datetime) -> str:
    """'Today', 'Yesterday', '4 days ago', '2 weeks ago', '3 months ago'."""
    if at is None:
        return "Not yet"
    days = (now.date() - at.astimezone(UTC).date()).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Yesterday"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    return f"{days // 30} months ago"


def ago_phrase(at: datetime | None, now: datetime) -> str:
    """'2 days ago' for sentences ('today', 'yesterday' in lower case)."""
    value = relative(at, now)
    return value[0].lower() + value[1:] if value in ("Today", "Yesterday") else value


_ACCOUNTS = text(
    """
    SELECT a.id, a.domain, coalesce(a.name, a.domain) AS name, a.lanes, a.refresh_cadence,
      a.created_at,
      a.next_refresh_at, a.origin,
      (SELECT array_agg(f ORDER BY f DESC) FROM (
          SELECT r.finished_at AS f FROM account_runs r
           WHERE r.account_id = a.id AND r.status IN ('COMPLETED', 'PARTIAL')
           ORDER BY r.finished_at DESC LIMIT 2) x) AS refreshes,
      (SELECT count(*) FROM account_runs r
        WHERE r.account_id = a.id AND r.status IN ('COMPLETED', 'PARTIAL'))
        AS refresh_count,
      (SELECT j.status FROM research_jobs j WHERE j.account_id = a.id
        ORDER BY j.created_at DESC LIMIT 1)
        AS job_status,
      (SELECT j.finished_at FROM research_jobs j WHERE j.account_id = a.id AND j.status = 'done'
        ORDER BY j.finished_at DESC LIMIT 1) AS job_done
    FROM accounts a
    WHERE a.deleted_at IS NULL
    """
)
_ONE_ACCOUNT = text(_ACCOUNTS.text + " AND a.id = :account")


def _state(row: Any, new_signals: int) -> str:
    if row.job_status in ("queued", "running"):
        return "researching"
    if row.job_status == "waiting_credit_limit":
        return "waiting_credit_limit"
    if row.job_status == "failed":
        return "failed"
    if not row.refresh_count:
        return "not_researched"
    if row.refresh_count == 1:
        return "first_scan"
    return "new_signals" if new_signals else "no_change"


def _last_researched(row: Any) -> datetime | None:
    values = [v for v in ((row.refreshes or [None])[0], row.job_done) if v is not None]
    return max(values) if values else None


async def _new_signals_of(session: Any, rows: Sequence[Any]) -> dict[UUID, int]:
    """Signals detected since the previous refresh, for every account that has had one, in one
    query."""
    asked = {r.id: r.refreshes[1] for r in rows if r.refreshes and len(r.refreshes) > 1}
    if not asked:
        return {}
    found = (
        await session.execute(
            text(
                "SELECT q.a AS account_id, count(s.*) AS n FROM unnest(CAST(:ids AS uuid[]),"
                " CAST(:since AS timestamptz[])) AS q(a, p) JOIN signal_events s"
                " ON s.account_id = q.a AND s.detected_at > q.p GROUP BY q.a"
            ),
            {"ids": list(asked), "since": list(asked.values())},
        )
    ).all()
    return {r.account_id: int(r.n) for r in found}


async def account_rows(
    session: Any, now: datetime, account_id: UUID | None = None
) -> list[dict[str, Any]]:
    """Every account (or just ``account_id``) with its sub-line state, lanes, last researched
    and refresh cadence, most news first (new signals, then most recently researched)."""
    rows = (
        (await session.execute(_ONE_ACCOUNT, {"account": account_id})).all()
        if account_id is not None
        else (await session.execute(_ACCOUNTS)).all()
    )
    signals = await _new_signals_of(session, rows)
    out = []
    for row in rows:
        new = signals.get(row.id, 0)
        last = _last_researched(row)
        cadence = row.refresh_cadence or "off"
        out.append(
            {
                "id": str(row.id),
                "domain": row.domain,
                "name": row.name,
                "lanes": list(row.lanes or []),
                "cadence": cadence,
                "cadence_label": CADENCE_LABEL.get(cadence, "Off"),
                "last_researched_at": last.isoformat() if last else None,
                "last_researched_label": relative(last, now),
                "state": _state(row, new),
                "new_signals": new,
            }
        )
    out.sort(
        key=lambda r: (
            -r["new_signals"],
            -(
                datetime.fromisoformat(r["last_researched_at"]).timestamp()
                if r["last_researched_at"]
                else 0
            ),
            r["name"],
        )
    )
    return out


def _sentences(value: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", value.strip()) if s]


async def workspace(session: Any, account_id: UUID, user_id: str, now: datetime) -> dict[str, Any]:
    """The Account workspace read model (the account must already be resolved in this tenant)."""
    acct = (await account_rows(session, now, account_id))[0]
    runs = (
        await session.execute(
            text(
                "SELECT id, finished_at FROM account_runs WHERE account_id = :a AND status IN"
                " ('COMPLETED', 'PARTIAL')"
                " ORDER BY finished_at DESC LIMIT 2"
            ),
            {"a": account_id},
        )
    ).all()
    latest = runs[0] if runs else None
    previous = runs[1] if len(runs) > 1 else None

    narrative = None
    section = (
        await session.execute(
            text(
                # the research's own narrative, or the one read or written from the client's
                # uploaded files (a file-import or file-mode run): private, shown only here
                "SELECT text, cited_source_codes FROM report_sections r WHERE account_id = :a AND"
                " kind = 'narrative'"
                " AND (visibility = 'public' OR EXISTS (SELECT 1 FROM account_runs x"
                "   WHERE x.id = r.run_id AND (x.finished_reason = 'file_import'"
                "   OR x.run_input->>'input_mode' = 'file')))"
                # a statement before a question (the Summary file refuses a question as its
                # opening: Olympus 2026-11-07 showed "Is the account Olympus America, Inc. ...?");
                # a question still beats a blank opening when it is all there is
                f" ORDER BY {part_refresh_last('r')}, btrim(text) ~ '[?]$',"
                " created_at DESC, version DESC, position LIMIT 1"
            ),
            {"a": account_id},
        )
    ).first()
    if section is not None:
        codes = list(section.cited_source_codes or [])
        found = {
            r.source_code: r
            for r in (
                await session.execute(
                    text(
                        "SELECT source_code, title, url FROM sources WHERE account_id = :a AND"
                        " source_code = ANY(:c)"
                    ),
                    {"a": account_id, "c": codes},
                )
            ).all()
        }
        sentences = _sentences(section.text)
        narrative = {
            "sentences": [
                {
                    "text": s,
                    "source_code": codes[i] if i < len(codes) else None,
                    "source_title": found[codes[i]].title
                    if i < len(codes) and codes[i] in found
                    else None,
                    "source_url": _original_page(found[codes[i]].url)
                    if i < len(codes) and codes[i] in found
                    else None,
                }
                for i, s in enumerate(sentences)
            ]
        }

    def fact_row(r: Any, when_label: str) -> dict[str, Any]:
        """One finding as the workspace table row shows it."""
        return {
            "grade": r.product_grade,
            "text": r.v,
            "when": when_label,
            "source_url": _original_page(r.url),
            "source_code": r.source_ref,
        }

    facts_sql = (
        "SELECT f.id, coalesce(f.value_text, f.value_json->>'text') AS v, f.product_grade,"  # nosec B608 -- fixed SQL fragments; values are bound.
        " f.attribute_key, f.lane,"
        " f.flags, f.run_id, f.created_at, e.url, e.evidence_date, e.source_ref"
        " FROM facts f LEFT JOIN LATERAL (SELECT url, evidence_date, source_ref FROM fact_evidence"
        " x"
        "   WHERE x.fact_id = f.id ORDER BY evidence_date DESC NULLS LAST LIMIT 1) e ON true"
        # a finding read from the client's own uploaded file is theirs to see on their own account
        # page (never in a shareable file): the same rule as the deep-dive counts
        " WHERE f.account_id = :a AND f.status = 'current' AND f.superseded_at IS NULL AND"
        " (f.visibility = 'public' OR f.flags @> CAST('[\"from_uploaded_file\"]' AS jsonb))"
        + NEWEST_FACT
    )
    facts = (await session.execute(text(facts_sql), {"a": account_id})).all()
    keyed = [f for f in facts if "key_finding" in (f.flags or [])]
    if not keyed:
        keyed = [f for f in facts if not str(f.attribute_key).startswith("news.")]
    keyed.sort(
        key=lambda f: (GRADE_RANK.get(f.product_grade or "", 9), -(f.created_at.timestamp()))
    )
    findings = []
    for f in keyed[:5]:
        when = (
            "This scan"
            if f.product_grade == "hypothesis"
            else day_month(f.evidence_date or f.created_at)
        )
        findings.append(fact_row(f, when))

    changes = None
    if previous is not None and latest is not None:
        moved = [
            f
            for f in facts
            if f.run_id == latest.id
            and f.product_grade in ("confirmed", "probable")
            and not str(f.attribute_key).startswith("news.")
        ]
        moved.sort(key=lambda f: f.evidence_date or f.created_at, reverse=True)
        # one line per statement: the same finding from two sources is not two changes
        seen_text: set[str] = set()
        distinct = []
        for f in moved:
            key = " ".join(str(f.v or "").split()).casefold()
            if key not in seen_text:
                seen_text.add(key)
                distinct.append(f)
        moved = distinct
        changed_lanes = {f.lane for f in moved if f.lane}
        all_lanes = (
            (
                await session.execute(
                    text(
                        "SELECT DISTINCT unnest(lanes) AS lane FROM accounts"
                        " WHERE deleted_at IS NULL"
                    )
                )
            )
            .scalars()
            .all()
        )
        quiet = [
            lane
            for lane in sorted(all_lanes)
            if not any(LANE_LABEL.get(c, c) == lane for c in changed_lanes)
        ]
        changes = {
            "since": day_month(previous.finished_at),
            "rows": [
                fact_row(f, relative(f.evidence_date or f.created_at, now)) for f in moved[:6]
            ],
            "quiet_lanes": quiet,
        }

    counts = (
        await session.execute(
            text(
                "SELECT"  # nosec B608 -- fixed SQL fragments; values are bound.
                " ((SELECT count(*) FROM signal_events WHERE tenant_id = :t AND account_id = :a)"
                f"  + (SELECT count(*) FROM facts f WHERE {FACT_CLASS['signals']})) AS signals,"
                " ((SELECT count(*) FROM people p WHERE p.tenant_id = :t AND p.account_id = :a AND"
                " NOT p.excluded AND"
                " p.merged_into IS NULL"
                f"   AND p.erased_at IS NULL AND {internal_people_sql()}"
                f"   {NEWEST_PERSON})"
                f"  + (SELECT count(*) FROM facts f WHERE {FACT_CLASS['people']})) AS people,"
                # every posting, open and closed (owner decision 2026-11-06: the counter read 0
                # while the research held 144 postings, most of them closed)
                " ((SELECT count(*) FROM job_postings j WHERE j.tenant_id = :t AND j.account_id ="
                f" :a {NEWEST_POSTING})"
                f"  + (SELECT count(*) FROM facts f WHERE {FACT_CLASS['jobs']})) AS jobs,"
                " ((SELECT count(*) FROM tech_detections t WHERE t.tenant_id = :t AND t.account_id"
                " = :a"
                f" {NEWEST_DETECTION})"
                f"  + (SELECT count(*) FROM facts f WHERE {FACT_CLASS['tech']})) AS tech,"
                f" ((SELECT count(*) FROM facts f WHERE {FACT_CLASS['news']})"
                "  + (SELECT count(*) FROM news_events n WHERE n.tenant_id = :t AND n.account_id ="
                " :a)) AS news,"
                " ((SELECT count(*) FROM sources WHERE tenant_id = :t AND account_id = :a)"
                "  + (SELECT count(DISTINCT t.response->'args'->>'url') FROM tool_calls t"
                "   JOIN account_runs r ON r.tenant_id = t.tenant_id AND r.id = t.run_id WHERE"
                " r.tenant_id = :t AND r.account_id = :a"
                "   AND t.tool IN ('p_search:read_page', 'p_scrape:scrape_job_page')"
                "   AND t.response->'args'->>'url' IS NOT NULL AND NOT EXISTS (SELECT 1 FROM"
                "   sources x WHERE x.tenant_id = :t AND x.account_id = :a AND x.url ="
                " t.response->'args'->>'url'))"
                # the client's uploaded files that facts cite (documents.deliverables)
                "  + (SELECT count(*) FROM documents d WHERE d.tenant_id = :t AND d.account_id = :a"
                " AND EXISTS ("
                "   SELECT 1 FROM fact_evidence e JOIN facts f ON f.tenant_id = e.tenant_id"
                "   AND f.id = e.fact_id WHERE e.tenant_id = d.tenant_id AND e.document_id = d.id"
                "   AND f.account_id = :a AND f.status = 'current')))"
                " AS sources,"
                " (SELECT count(*) FROM tool_calls t JOIN account_runs r ON r.tenant_id ="
                " t.tenant_id AND r.id = t.run_id"
                "   WHERE r.tenant_id = :t AND r.account_id = :a AND t.tool ="
                " 'p_search:web_search') AS searches,"
                " (SELECT count(*) FROM document_members m JOIN documents d ON d.tenant_id ="
                "   m.tenant_id AND d.id = m.document_id WHERE m.tenant_id = :t"
                "   AND d.account_id = :a) AS files,"
                # the live sheets of an imported workspace (phase 5): teams of the newest run
                # that has any, connections, the newest Summary's sections, calls and notes
                " (SELECT count(*) FROM platform_teams t WHERE t.tenant_id = :t AND t.account_id ="
                " :a AND t.run_id ="
                "   (SELECT r.run_id FROM platform_teams r WHERE r.tenant_id = :t AND r.account_id"
                " = :a"
                "   ORDER BY r.created_at DESC LIMIT 1)) AS teams,"
                " (SELECT count(*) FROM org_connections c WHERE c.tenant_id = :t AND c.account_id ="
                " :a) AS connections,"
                " (SELECT count(*) FROM report_sections r WHERE r.tenant_id = :t AND r.account_id ="
                " :a"
                "   AND (r.run_id, r.version) = (SELECT x.run_id,"
                "   x.version FROM report_sections x WHERE x.tenant_id = :t AND x.account_id = :a"
                f"   ORDER BY {part_refresh_last('x')}, x.created_at DESC, x.version DESC LIMIT 1))"
                " AS findings,"
                " ((SELECT count(*) FROM tool_calls t JOIN account_runs r ON r.tenant_id ="
                " t.tenant_id AND r.id = t.run_id"
                "   WHERE r.tenant_id = :t AND r.account_id = :a)"
                "  + (SELECT count(*) FROM pull_log_calls c WHERE c.tenant_id = :t AND c.account_id"
                " = :a)) AS calls,"
                " (SELECT count(*) FROM research_notes n WHERE n.tenant_id = :t AND n.account_id ="
                " :a) AS notes,"
                " (SELECT count(*) FILTER (WHERE c.status = 'done') FROM coverage_rows c WHERE"
                "   c.tenant_id = :t AND c.run_id = (SELECT r.id FROM account_runs r WHERE"
                " r.tenant_id = :t AND r.account_id = :a"
                "   AND r.status IN ('COMPLETED', 'PARTIAL', 'BUDGET_EXCEEDED')"
                "   AND EXISTS (SELECT 1 FROM coverage_rows x WHERE x.tenant_id = r.tenant_id"
                "   AND x.run_id = r.id)"
                "   ORDER BY r.finished_at DESC NULLS LAST LIMIT 1)"
                f"   AND {PLANNED}) AS coverage_done,"
                " (SELECT count(*) FROM coverage_rows c WHERE"
                "   c.tenant_id = :t AND c.run_id = (SELECT r.id FROM account_runs r WHERE"
                " r.tenant_id = :t AND r.account_id = :a"
                "   AND r.status IN ('COMPLETED', 'PARTIAL', 'BUDGET_EXCEEDED')"
                "   AND EXISTS (SELECT 1 FROM coverage_rows x WHERE x.tenant_id = r.tenant_id"
                "   AND x.run_id = r.id)"
                "   ORDER BY r.finished_at DESC NULLS LAST LIMIT 1)"
                f"   AND {PLANNED}) AS coverage_total"
            ),
            {"a": account_id, "t": await session_tenant(session)},
        )
    ).one()

    files = await deliverables(session, account_id)
    job = (
        await session.execute(
            text(
                "SELECT j.id, j.kind, j.scope, j.status, j.topic, j.run_id,"  # nosec B608 -- fixed SQL fragments; values are bound.
                f" (SELECT count(*) FROM coverage_rows c WHERE c.run_id = j.run_id AND {PLANNED})"
                " AS total,"
                " (SELECT count(*) FROM coverage_rows c WHERE c.run_id = j.run_id"
                f"   AND c.status IS NOT NULL AND {PLANNED}) AS done"
                " FROM research_jobs j WHERE j.account_id = :a"
                " AND j.status IN ('waiting_credit_limit', 'queued', 'running')"
                # a job whose run already ended is not running (the sweep settles it)
                " AND NOT EXISTS (SELECT 1 FROM account_runs r WHERE r.id = j.run_id AND r.status"
                "  IN ('COMPLETED', 'PARTIAL', 'BUDGET_EXCEEDED', 'FAILED', 'CANCELED'))"
                " ORDER BY j.created_at DESC LIMIT 1"
            ),
            {"a": account_id},
        )
    ).first()
    stopped = None if job is not None else await stopped_run(session, account_id)
    last = (
        datetime.fromisoformat(acct["last_researched_at"]) if acct["last_researched_at"] else None
    )
    first_scan = previous is None
    if last is None:
        subline = "Not researched yet · Web: not used for answers"
    elif first_scan:
        subline = f"First scan · {day_month(last)} · Web: not used for answers"
    else:
        cadence = acct["cadence_label"]
        sched = "No schedule" if acct["cadence"] == "off" else f"{cadence} schedule"
        subline = f"Refreshed {ago_phrase(last, now)} · {sched} · Web: not used for answers"
    return {
        **acct,
        "subline": subline,
        "first_scan": first_scan,
        "narrative": narrative,
        "findings": findings,
        "changes": changes,
        "counts": dict(counts._mapping),
        "files": files,
        "gaps": await coverage_gaps(session, account_id) if job is None else [],
        "open_job": None
        if job is None
        else {
            "id": str(job.id),
            "kind": job.kind,
            "scope": job.scope,
            "status": job.status,
            "topic": job.topic,
            "steps_done": int(job.done or 0),
            "steps_total": int(job.total or 0),
            "run_id": str(job.run_id) if job.run_id else None,
        },
        "stopped_run": stopped,
        "import_problems": await import_problems(session, account_id),
    }


async def import_problems(session: Any, account_id: UUID) -> list[dict[str, Any]]:
    """The parts of an uploaded file's import that failed or are still pending or running, and
    an import that failed as a whole before any part ran (part ``import``), for the banner on the
    account page (at most 10): the document, the part, its status, the rows not imported and the
    first reason (a row number and a rule, or the error class). One import run per document, so
    this is the latest import of each document bound to the account; a file uploaded again under
    the same name to the same account replaces the earlier one here."""
    rows = (
        await session.execute(
            text(
                "WITH current_runs AS ("
                " SELECT ir.id, ir.tenant_id, ir.status, ir.error_message, d.name,"
                "   coalesce(ir.started_at, ir.created_at) AS at"
                " FROM import_runs ir JOIN documents d ON d.tenant_id = ir.tenant_id"
                "   AND d.id = ir.document_id"
                " WHERE ir.tenant_id = :t AND ir.account_id = :a AND d.account_id = :a"
                "   AND NOT EXISTS (SELECT 1 FROM documents d2 WHERE d2.tenant_id = d.tenant_id"
                "     AND d2.account_id = d.account_id AND d2.name = d.name"
                "     AND d2.created_at > d.created_at))"
                " SELECT * FROM ("
                " SELECT c.name AS document_name, p.part, p.status, p.rows_skipped,"
                "   coalesce(p.error_message, p.skip_reasons ->> 0) AS reason, c.at"
                " FROM current_runs c JOIN import_run_parts p ON p.tenant_id = c.tenant_id"
                "   AND p.import_run_id = c.id"
                " WHERE p.status IN ('failed', 'pending', 'running')"
                " UNION ALL"
                " SELECT c.name, 'import', c.status, 0, c.error_message, c.at FROM current_runs c"
                " WHERE c.status = 'failed' AND NOT EXISTS (SELECT 1 FROM import_run_parts p"
                "   WHERE p.tenant_id = c.tenant_id AND p.import_run_id = c.id"
                "   AND p.status = 'failed')"
                ") problems ORDER BY status = 'failed' DESC, at DESC, part LIMIT 10"
            ),
            {"t": await session_tenant(session), "a": account_id},
        )
    ).all()
    return [
        {
            "document_name": str(r.document_name),
            "part": str(r.part),
            "status": str(r.status),
            "rows_skipped": int(r.rows_skipped),
            "reason": str(r.reason) if r.reason else None,
        }
        for r in rows
    ]


# The research graph records what it finds as graded facts keyed ``<class>.<step>.<attribute>``
# (it does not fill the people, job_postings and tech_detections tables of the older workflow),
# so each deep-dive category also counts and lists the public facts of its class (audit
# 2026-10-15: every category showed 0 after a finished research).
# public facts, and the client's own rows imported from an uploaded deliverable workbook
# (private, Internal; documents.deliverables, 2026-10-16): the client sees its own material
# the words a job title opens with (a people fact whose value opens with one and names nobody
# after a comma is a title, not a person)
_TITLE_WORDS = (
    "vice|senior|sr|chief|head|director|principal|executive|general|global|group|associate"
    "|assistant|svp|evp|vp|avp|cto|cio|ceo|cfo|coo|cmo|cdo|ciso|manager|lead|president|managing"
)


def title_only(value_sql: str) -> str:
    """SQL that is true when ``value_sql`` is only a job title ("Vice President of Technology
    Product Management", "Principal Product Manager, Merge by Merative"): it opens with a title
    word and names nobody after a comma. New runs drop such people facts at extraction
    (``agents.people_names.names_a_person``); this keeps the ones stored before off the People
    table. A title followed by a name ("Chief Information Officer, Jane Doe") is a person."""
    return (
        f"({value_sql} ~* '^({_TITLE_WORDS})\\M'"
        f" AND {value_sql} !~ ',\\s*[A-Z][A-Za-z.''-]+(\\s+[A-Z][A-Za-z.''-]+){{1,3}}\\s*$')"
    )


_FACT_PUBLIC = (
    "f.tenant_id = :t AND f.account_id = :a AND f.status = 'current' AND (f.visibility = 'public'"
    " OR f.flags @> CAST('[\"from_uploaded_file\"]' AS jsonb))" + NEWEST_FACT
)
FACT_CLASS = {
    # facts the people step records about a company or a person (ticker, switchboard, media
    # mailbox, prior employers) are details, not people
    "people": (
        f"{_FACT_PUBLIC} AND f.attribute_key LIKE 'people.%'"
        " AND split_part(f.attribute_key, '.', 3) <> 'company'"
        " AND f.attribute_key !~ '[.](prior_employers?|[a-z_]*email|[a-z_]*phone)$'"
        # a value that is only a title names nobody: it is not a person (``title_only``)
        " AND NOT " + title_only("coalesce(f.value_text, f.value_json->>'text', '')")
    ),
    # a count fact ("13" openings in a lane) is not a posting: a role needs a letter in it
    "jobs": (
        f"{_FACT_PUBLIC} AND f.attribute_key LIKE 'jobs.%'"
        " AND coalesce(coalesce(f.value_text, f.value_json->>'text'), '')"
        " !~ '^[0-9.,% ]+$'"
    ),
    "tech": f"{_FACT_PUBLIC} AND f.attribute_key LIKE 'tech.%'",
    "news": (
        f"{_FACT_PUBLIC} AND (f.attribute_key LIKE 'news.%' OR split_part(f.attribute_key, '.', 2)"
        " IN ('company_news', 'leadership_changes', 'competitor_moves',"
        " 'regulatory_quality_events', 'events_sponsored'))"
    ),
}
# the research's own findings shown as signals beside the monitoring events (owner decision
# 2026-11-06: every account's Signals screen read 0 after a research, because only a scheduled
# refresh or an uploaded workspace writes ``signal_events``)
FACT_CLASS["signals"] = FACT_CLASS["news"]

# What a step that could not finish says on the account page (tracker reason codes).
GAP_TEXT = {
    "consent_required": "needs your consent for people data",
    "waiting_on_account_owner": "waits on information only you hold",
    "pull_not_approved": "a follow-up search was not approved within the credit limit",
    "not_planned": "outside this research's scope",
    "lane_not_in_scope": "outside the lanes you chose",
    "source_unavailable": "the source could not be reached",
    "source_failed": "the source failed",
    "source_rate_limited": "the source limited our requests",
    "budget_cap": "the credit limit was reached",
    "credits_exhausted": "the credits ran out",
    "no_cap": "no credit limit was confirmed",
    "cap_reached": "the credit limit was reached",
    "not_available_yet": "not available yet",
    "fewer_than_expected": "fewer results than expected",
    "postings_not_read": "some matching postings were not read yet",
    "feed_incomplete": "the job feed did not return every posting",
    "counts_inconsistent": "the posting counts did not add up",
    "careers_site_not_found": "no careers site was found for this company",
    "claims_dropped": "what was found did not pass checking",
}


def part_refresh_last(alias: str) -> str:
    """SQL ranking a refresh of part of the checklist (its run input names ``scope_rows``) after
    a run of the whole checklist: ``ORDER BY`` this first. Such a refresh writes its Summary from
    its own few rows; the files keep the research's (``exports.carry``) and so does the page,
    which showed a topic refresh's 5-row opening for Olympus (staging 2026-11-07)."""
    return (
        "COALESCE((SELECT p.run_input ? 'scope_rows' FROM account_runs p"  # nosec B608 -- fixed SQL fragment.
        f" WHERE p.id = {alias}.run_id), false)"
    )


def gap_text(code: str) -> str:
    """One readable reason for a step that could not finish."""
    return GAP_TEXT.get(code, code.replace("_", " "))


async def coverage_gaps(session: Any, account_id: UUID) -> list[dict[str, Any]]:
    """The steps of the latest finished research that could not be finished, by reason."""
    rows = (
        await session.execute(
            text(
                "SELECT coalesce(c.reason_code, 'unknown') AS code, count(*) AS n"  # nosec B608 -- fixed SQL fragments; values are bound.
                " FROM coverage_rows c WHERE c.run_id = (SELECT r.id FROM account_runs r"
                "   WHERE r.account_id = :a AND r.status IN ('COMPLETED', 'PARTIAL',"
                "   'BUDGET_EXCEEDED') AND EXISTS (SELECT 1 FROM coverage_rows x WHERE"
                "   x.run_id = r.id) ORDER BY r.finished_at DESC NULLS LAST LIMIT 1)"
                " AND c.status IS DISTINCT FROM 'done' AND c.status IS DISTINCT FROM 'none'"
                f" AND {PLANNED}"
                " GROUP BY 1 ORDER BY 2 DESC, 1"
            ),
            {"a": account_id},
        )
    ).all()
    return [{"reason": gap_text(str(r.code)), "count": int(r.n)} for r in rows]


STOP_TEXT = {
    "DollarCapReached": "It reached the spending cap for one research.",
    "RepeatedCallStopped": "It kept paying for the same search, so it was stopped.",
    "worker_lost": "It was interrupted.",
    "stalled": "It stopped making progress.",
    "credit_cap_reached": "It reached its spending limit.",
    # the AI model provider refused (audit 2026-10-15): say so, so the user restores access
    # before resuming instead of paying for a resume that stops again
    "model_credits_exhausted": (
        "The AI model provider refused: its usage limit or credit ran out. Restore it, then resume."
    ),
    "model_auth_failed": "The AI model provider refused the key. Check the key, then resume.",
    "model_unavailable": "The AI model provider was unavailable. Resume in a little while.",
    "model_not_configured": "No AI model is set up for this workspace.",
    "model_refused": "The AI model declined part of the work.",
    # the data-class guard (ADR-0133) refused to send the client's data to a model that may keep it
    "model_data_class_refused": (
        "The chosen AI model may not receive this workspace's own data, so nothing was sent to it."
        " Choose a model approved for client data, then resume."
    ),
    "BadRequestError": (
        "The AI model provider refused the request, often because its usage limit was reached."
        " Restore it, then resume."
    ),
    "gate_failed": (
        "Some steps could not be finished, so the quality check held the files back."
        " Open Coverage to see which steps and why."
    ),
    # the gate held back some files and delivered the rest (agents.graph FILES_WITHHELD)
    "files_withheld": (
        "The quality check held back some files; the other files are ready."
        " Open Coverage to see which."
    ),
    "steps_waiting": "Some steps could not be finished. Open Coverage to see which steps and why.",
    # the tracker's completion verdict refused COMPLETED (ADR-0128): finished with gaps
    "tracker_verdict": (
        "Some steps could not be finished. Open Coverage to see which steps and why."
    ),
    "pull_only": "This run only collected data; nothing was written up yet.",
    # the run graded no new fact (audit 2026-10-20): finished, nothing to resume
    "no_new_findings": "Nothing new was found on the web for this research.",
    "slice_ceiling": (
        "It ran longer than the platform allows for one go. What it found is kept."
        " It does not continue by itself: press Resume to go on."
    ),
    "budget_cap": "It reached the credits confirmed for it.",
    "no_progress": (
        "It made no progress in three tries in a row, so it was stopped. What it found is kept."
        " Press Resume to try again."
    ),
    "token_guard_stopped": (  # nosec B105 -- a label, not a credential.
        "It reached the AI model allowance for one research and was not approved to go on."
        " What it found is kept. Press Resume to continue."
    ),
    "entity_mismatch": (
        "The company's own website names a different company than the one entered, so the"
        " research stopped before anything was charged. Check the website address and start again."
    ),
    "invalid_setup": (
        "The workspace settings do not fit this research, so it did not start."
        " Nothing was charged. Ask an administrator to check them."
    ),
    "no_pulled_data": "There was no collected data to write up. Nothing was charged.",
    "stale_pulls": (
        "The collected data is out of date, so nothing was written up. Nothing was charged."
        " Start a new refresh."
    ),
    "sense_check_budget_exceeded": (
        "The final check used up its allowance before it finished. What was found is kept."
        " Press Resume to try again."
    ),
    "distill_budget_exceeded": (
        "The write-up step used up its allowance before it finished. What was found is kept."
        " Press Resume to try again."
    ),
    "sense_check_missing": "The final check did not run. Press Resume to try again.",
    "sense_check_stale": (
        "The findings changed after the final check ran. Press Resume to try again."
    ),
    "manifest_changed": (
        "The collected data changed while the files were being prepared. Press Resume to try again."
    ),
    "third_party_domain": "A client file would have named a data provider, so it was held back.",
    "client_output_leak": (
        "A client file would have carried a term it may never carry, so it was held back."
    ),
    "approval_timeout": (
        "Nobody answered the approval request in time, so the research was canceled."
        " Nothing was charged."
    ),
    "canceled_by_user": "You stopped this research.",
}
# A data or AI provider that stayed down or kept limiting requests past the retries, and a
# connection that dropped: the platform continues such a research by itself (auto_resume
# PROVIDER_OUTAGE_STOPS and INFRASTRUCTURE_STOPS; a test keeps the two lists in step)
PROVIDER_OUTAGE_TEXT = (
    "A data or AI provider was down or limiting requests for too long. What it found is kept."
    " The platform continues the research by itself; you can also press Resume."
)
CONNECTION_DROPPED_TEXT = (
    "A connection dropped. What it found is kept."
    " The platform continues the research by itself; you can also press Resume."
)
STOP_TEXT.update(
    {
        code: PROVIDER_OUTAGE_TEXT
        for code in (
            "RetryableProviderError",
            "RateLimited",
            "ProviderJobTimeout",
            "CallInFlight",
            "Overloaded",
            "CircuitOpen",
        )
    }
)
STOP_TEXT.update(
    {
        code: CONNECTION_DROPPED_TEXT
        for code in (
            "OperationalError",
            "InterfaceError",
            "TimeoutError",
            "ConnectionError",
            "ConnectionResetError",
            "ConnectionRefusedError",
            "ConnectionAbortedError",
            "BrokenPipeError",
            "ServiceUnavailable",
            "GatewayTimeout",
            "DeadlineExceeded",
        )
    }
)
# A coverage row ``c`` inside its run's scope. A scoped research (job postings, people, one
# topic) records the rows it pulls in ``account_runs.run_input.scope_rows`` and still writes all
# 33 rows, marking the others "not planned" at once; counting every row showed "33 of 33 steps"
# for a 3-step research (owner report 2026-10-21). The steps a research shows, counts and lists
# as gaps are the rows in its scope; a full research (no scope_rows) keeps all 33.
PLANNED = (
    "NOT EXISTS (SELECT 1 FROM account_runs x WHERE x.tenant_id = c.tenant_id AND x.id = c.run_id"
    " AND jsonb_typeof(x.run_input->'scope_rows') = 'array'"
    " AND NOT jsonb_exists(x.run_input->'scope_rows', c.row_key))"
)
# "build_failed:<why>": the files could not be built from what was found (audit 2026-10-15)
BUILD_FAILED_TEXT = "The files could not be built from what was found ({why})."
# the rules a build fails on (exports.builder), in words; an unknown rule shows as its code
BUILD_RULE_TEXT = {
    "missing_narrative": "no opening narrative could be written from the findings",
    "banned_dash": "a dash used as punctuation in a client file",
    "orphan_citation": "a citation with no matching source",
    "unregistered_number": "a number no count or source backs",
    "leak": "a term a client file may never carry",
    "tool_name": "a data provider named in a client file",
    "cost_word": "a word a client file may never carry",
    "data_tool_host": "a link to a data provider in a client file",
    "internal_id": "an internal identifier in a client file",
    "render_failed": "the file could not be written",
    "missing_tracker": "no coverage table to print",
    "row_cap": "more rows than a file may hold",
    "missing_source_code": "a row without a registered source",
    "missing_url": "a row without a link",
    "bad_url": "a row whose link cannot be used",
    "stale_phrase": "a phrase that must no longer appear",
    "excluded_name": "a name that must not appear",
}


# what each quality-gate check means to the person reading the card (documents.gate)
GATE_CHECK_TEXT = {
    "tracker_rows": "a step without a status or a reason",
    "fact_citations": "a finding without a source",
    "client_dashes": "a dash used as punctuation in a client file",
    "client_terms": "a term a client file may never carry",
    "client_output": "text the distribution rules do not let this file carry",
    "third_party_domains": "a data provider named in a client file",
    "rows_dropped": "a row lost between the data and a file",
    "leak_check": "text too close to your uploaded files",
    "deliverable_set": "a client file missing",
    "internal_label": "the internal file's label",
    "gate_not_configured": "no quality gate configured",
}
GATE_FAILED_TEXT = "The quality check held the files back: {why}. Open Coverage to see each step."


def gate_text(defects: Sequence[Mapping[str, Any]]) -> str:
    """The quality check's failing checks in words, with counts ("3 findings without a
    source, 1 dash ..."), from the stored verdict's defects; the generic text without them."""
    counts = Counter(str(d.get("check") or "") for d in defects if d.get("check"))
    if not counts:
        return STOP_TEXT["gate_failed"]
    parts = []
    for check, n in counts.most_common():
        words = GATE_CHECK_TEXT.get(check, check.replace("_", " "))
        parts.append(f"{n} x {words}" if n > 1 else words)
    return GATE_FAILED_TEXT.format(why="; ".join(parts))


WITHHELD_TEXT = "The quality check held back {files}: {why}. The other files are ready."


def withheld_text(defects: Sequence[Mapping[str, Any]]) -> str:
    """Which files the quality check held back and why, in words ("Job postings (text too close
    to your uploaded files)"), from the stored verdict's defects; the generic text without
    them."""
    from documents.gate import withheld_files

    held = withheld_files(defects)
    if not held:
        return STOP_TEXT["files_withheld"]
    # the rule a file the builder left out was held under (``client_output`` items read
    # ``kind:rule``), in words
    rules: dict[str, list[str]] = {}
    for d in defects:
        kind, _, rule = str(d.get("item") or "").partition(":")
        if d.get("check") == "client_output" and rule:
            rules.setdefault(kind, []).extend(r for r in rule.split(",") if r)
    parts = []
    for kind in DELIVERABLE_ORDER:
        if kind in held:
            words = [
                BUILD_RULE_TEXT.get(r, r.replace("_", " "))
                for r in dict.fromkeys(rules.get(kind, []))
            ]
            why = "; ".join(
                [
                    GATE_CHECK_TEXT.get(c, c.replace("_", " "))
                    for c in held[kind]
                    if c != "client_output"
                ]
                + words
                or [GATE_CHECK_TEXT["client_output"]]
            )
            parts.append(f"{DELIVERABLE_LABEL[kind]} ({why})")
    return WITHHELD_TEXT.format(files=", ".join(parts), why="see Coverage")


def stop_text(reason: str, defects: Sequence[Mapping[str, Any]] = ()) -> str:
    """What the user is told about a research that stopped: a known reason in words, a build
    failure with its cause, the quality check's failing checks, anything else with its code so
    it can be reported."""
    if reason == "gate_failed" and defects:
        return gate_text(defects)
    if reason == "files_withheld" and defects:
        return withheld_text(defects)
    if reason in STOP_TEXT:
        return STOP_TEXT[reason]
    if reason.startswith("build_failed"):
        rules = [r for r in reason.partition(":")[2].split(",") if r]
        why = "; ".join(BUILD_RULE_TEXT.get(r, r.replace("_", " ")) for r in rules)
        return BUILD_FAILED_TEXT.format(why=why or "no detail")
    return f"It stopped before finishing (reason: {reason.replace('_', ' ')})."


_ENDED = ("COMPLETED", "PARTIAL", "FAILED", "CANCELED", "BUDGET_EXCEEDED")
# a research that delivered its files with steps it could not finish (consent, waiting on the
# owner, not approved) is finished, not stopped: Coverage says which steps and why
_FINISHED_REASONS = (
    None,
    "completed",
    "canceled",
    "user_canceled",
    "steps_waiting",
    "tracker_verdict",
    "no_new_findings",
)
OPEN_JOB_STATUSES = ("waiting_credit_limit", "queued", "running")


def stalled(status: str | None, stalled_at: Any) -> bool:
    """A run the platform marked as making no progress and that has not ended."""
    return stalled_at is not None and status not in _ENDED


def resumable(status: str | None, finished_reason: str | None, stalled_at: Any) -> bool:
    """The one rule for offering Resume research on a run (account page, Research board, run
    page): it stalled, or it ended early (PARTIAL, FAILED, BUDGET_EXCEEDED) for a reason other
    than a cancel or a finish with waiting steps. The caller adds the account checks
    (``can_resume``): only the account's latest company run, and only with no job open."""
    ended_early = status in ("PARTIAL", "FAILED", "BUDGET_EXCEEDED")
    return stalled(status, stalled_at) or (ended_early and finished_reason not in _FINISHED_REASONS)


def resume_context(run: str) -> str:
    """Select-list SQL for a run aliased ``run`` (which must expose ``id`` and ``account_id``):
    ``is_latest`` (it is the account's latest company run) and ``job_open`` (the account has a
    job waiting, queued or running, which Resume would refuse with ``credits.JobOpen``)."""
    statuses = ", ".join(f"'{s}'" for s in OPEN_JOB_STATUSES)
    return (
        f"({run}.id = (SELECT l.id FROM account_runs l WHERE l.account_id = {run}.account_id"  # nosec B608 -- fixed SQL fragments; values are bound.
        " AND coalesce(l.input_mode, 'company') = 'company' ORDER BY l.created_at DESC LIMIT 1))"
        " AS is_latest,"
        f" EXISTS (SELECT 1 FROM research_jobs o WHERE o.account_id = {run}.account_id"
        f" AND o.status IN ({statuses})) AS job_open"
    )


def can_resume(
    status: str | None,
    finished_reason: str | None,
    stalled_at: Any,
    is_latest: bool | None,
    job_open: bool | None,
) -> bool:
    """Resume is offered on a run: ``resumable`` plus the ``resume_context`` columns."""
    return resumable(status, finished_reason, stalled_at) and bool(is_latest) and not job_open


async def stopped_run(session: Any, account_id: UUID) -> dict[str, Any] | None:
    """The account's latest research when it stopped before finishing (PARTIAL or FAILED with a
    reason other than a cancel) or stalled, with what it holds: the screen offers Resume research
    (owner decision 2026-10-14). None when the latest research finished or is moving."""
    row = (
        await session.execute(
            text(
                "SELECT r.id, r.status, r.finished_reason, r.stalled_at,"  # nosec B608 -- fixed SQL fragments; values are bound.
                " coalesce((r.run_input->>'replay_key_version')::int, 1) AS replay_version,"
                f" (SELECT count(*) FROM coverage_rows c WHERE c.run_id = r.id AND {PLANNED})"
                " AS total,"
                " (SELECT count(*) FROM coverage_rows c WHERE c.run_id = r.id"
                f"   AND c.status IN ('done', 'none') AND {PLANNED}) AS done"
                " FROM account_runs r WHERE r.account_id = :a AND coalesce(r.input_mode,"
                " 'company') = 'company' ORDER BY r.created_at DESC LIMIT 1"
            ),
            {"a": account_id},
        )
    ).first()
    if row is None:
        return None
    if not resumable(row.status, row.finished_reason, row.stalled_at):
        return None
    is_stalled = stalled(row.status, row.stalled_at)
    reason = "stalled" if is_stalled else str(row.finished_reason)
    defects: list[Any] = []
    if reason in ("gate_failed", "files_withheld"):  # the stored verdict says which checks
        found = await session.scalar(
            text(
                "SELECT defects FROM run_gate_results WHERE run_id = :r"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"r": row.id},
        )
        defects = list(found or [])
    return {
        "run_id": str(row.id),
        "stalled": is_stalled,
        "reason": stop_text(reason, defects),
        "steps_done": int(row.done),
        "steps_total": int(row.total),
        "replay_extra_usd": await replay_extra_usd(session, row.id, int(row.replay_version)),
    }


# what the source run paid: its priced model replies (the larger of the router's ledger and the
# research graph's reply log, as the token meter counts them) and its paid provider calls
_PAID = text(
    "SELECT GREATEST("
    " (SELECT COALESCE(sum(cost_usd), 0) FROM llm_calls WHERE run_id = r.id),"
    " (SELECT COALESCE(sum(cost_usd), 0) FROM model_reply_log"
    "  WHERE run_id = r.id AND served_from = 'model'))"
    " + COALESCE(r.spent_usd, 0) FROM account_runs r WHERE r.id = :r"
)


async def replay_extra_usd(session: Any, run_id: UUID, version: int) -> float | None:
    """How much more a resume of the run may cost because its replay keys are of an older
    version (ADR-0129), rounded up to the cent; None when its keys are current.

    An upper bound: everything the source run paid for (model replies and provider calls). A
    version bump names which keys changed, but a run does not record which of its calls used
    them, so the bound assumes none of them is reused; the calls whose keys did not change are
    still reused at no cost, so the resume usually costs less."""
    from account_intelligence.connectors.cache import replay_key_version

    if version >= replay_key_version():
        return None
    paid = Decimal(str(await session.scalar(_PAID, {"r": run_id}) or 0))
    return float(paid.quantize(Decimal("0.01"), rounding=ROUND_CEILING))


async def newest_files(session: Any, account_id: UUID) -> list[Any]:
    """The newest stored version of each deliverable kind of the account, with the latest stored
    gate verdict of its run (``verdict``, ``defects``) and the exclusion set it was built with
    (``xh``: the manifest's ``exclusion_hash``, None for a file built before it was recorded)."""
    return list(
        (
            await session.execute(
                text(
                    "SELECT DISTINCT ON (d.kind) d.id, d.kind, d.filename, d.size_bytes,"
                    " d.built_at, d.run_id, d.manifest_json->>'exclusion_hash' AS xh,"
                    # an uploaded workspace's own file (documents.workspace_import): shown as it
                    # is under any exclusion set, never rebuilt (exact copy, 2026-11-05)
                    " d.manifest_json->>'source' = 'workspace_import' AS imported,"
                    # the files the account's most recent build left out of its set, and its run:
                    # an older file of such a kind must not stand in for the missing one
                    " (SELECT x.manifest_json->'files_withheld' FROM deliverables x"
                    "   WHERE x.account_id = d.account_id"
                    "   ORDER BY x.built_at DESC, x.version DESC LIMIT 1) AS latest_withheld,"
                    " (SELECT x.run_id FROM deliverables x WHERE x.account_id = d.account_id"
                    "   ORDER BY x.built_at DESC, x.version DESC LIMIT 1) AS latest_run,"
                    " (SELECT g.defects FROM run_gate_results g WHERE g.run_id = d.run_id"
                    "   ORDER BY g.created_at DESC, g.id DESC LIMIT 1) AS defects,"
                    " (SELECT g.verdict FROM run_gate_results g WHERE g.run_id = d.run_id"
                    "   ORDER BY g.created_at DESC, g.id DESC LIMIT 1) AS verdict"
                    " FROM deliverables d"
                    " WHERE d.account_id = :a AND d.kind = ANY(:k) ORDER BY d.kind, d.version DESC"
                ),
                {"a": account_id, "k": DELIVERABLE_ORDER},
            )
        ).all()
    )


def _held_by_gate(row: Any) -> bool:
    """Whether the run's stored verdict holds this file back: a file-level defect naming its
    kind, or a run-level defect (a step without proof, a fact without a source, a client file
    missing), which holds back every file of the run (orchestration review 2026-10-30, F8)."""
    from documents.gate import builder_withheld, run_level_failed, withheld_files

    defects = list(row.defects or [])
    # a kind the account's latest build left out is held even before its gate verdict exists, and
    # the file of an earlier research never stands in for it
    left_out = builder_withheld([{"files_withheld": list(row.latest_withheld or [])}])
    if row.kind in left_out and row.run_id != row.latest_run:
        return True
    return row.kind in withheld_files(defects) or (
        row.verdict == "fail" and run_level_failed(defects)
    )


async def stale_kinds(session: Any, account_id: UUID) -> list[str]:
    """The kinds whose newest stored file was built under another exclusion set than the
    tenant's current one (F1): such a file is neither listed nor served until the account's
    files are built again. A file built before the hash was recorded counts as built with no
    exclusions, so it is stale exactly when the tenant has some."""
    from documents.gate import builder_withheld
    from exports.readers.exclusions import NO_EXCLUSIONS
    from views.materialize import current_exclusion_hash

    current = await current_exclusion_hash(session)
    # a kind the account's latest build left out has no newer file to wait for: building again
    # for it on every page view would only leave it out again
    return [
        r.kind
        for r in await newest_files(session, account_id)
        if (r.xh or NO_EXCLUSIONS) != current
        and not r.imported
        and r.kind not in builder_withheld([{"files_withheld": list(r.latest_withheld or [])}])
    ]


async def deliverables(session: Any, account_id: UUID) -> list[dict[str, Any]]:
    """The newest version of each deliverable of the account, in the design's order, without a
    file its run's quality check held back (the latest stored verdict: the file-level defects
    name their file, a run-level defect holds back all of the run's files,
    ``documents.gate.withheld_files`` and ``run_level_failed``) and without a file built under
    another exclusion set than the tenant's current one (``stale_kinds``): such a file is neither
    listed nor downloadable, and no older version stands in for it (it would not match the
    delivered files). The card says which file was held back and why (``withheld_text``)."""
    from exports.readers.exclusions import NO_EXCLUSIONS
    from views.materialize import current_exclusion_hash

    current = await current_exclusion_hash(session)
    rows = [
        r
        for r in await newest_files(session, account_id)
        if not _held_by_gate(r) and (r.imported or (r.xh or NO_EXCLUSIONS) == current)
    ]
    rows = sorted(rows, key=lambda r: DELIVERABLE_ORDER.index(r.kind))
    return [
        {
            "id": str(r.id),
            "kind": r.kind,
            "name": DELIVERABLE_LABEL[r.kind],
            "filename": r.filename,
            "meta": f"{file_type(r.filename)} · {size_label(int(r.size_bytes))}",
            "built_at": r.built_at.isoformat(),
        }
        for r in rows
    ]
