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
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text

from account_intelligence.replica.answers import LANE_LABEL, day_month
from account_intelligence.replica.settings import CADENCE_LABEL

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
    """
)


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


async def _new_signals(session: Any, account_id: UUID, previous: datetime | None) -> int:
    if previous is None:
        return 0
    return int(
        await session.scalar(
            text("SELECT count(*) FROM signal_events WHERE account_id = :a AND detected_at > :p"),
            {"a": account_id, "p": previous},
        )
        or 0
    )


async def account_rows(session: Any, now: datetime) -> list[dict[str, Any]]:
    """Every account with its sub-line state, lanes, last researched and refresh cadence,
    most news first (new signals, then most recently researched)."""
    out = []
    for row in (await session.execute(_ACCOUNTS)).all():
        refreshes = list(row.refreshes or [])
        previous = refreshes[1] if len(refreshes) > 1 else None
        new = await _new_signals(session, row.id, previous)
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
    rows = [r for r in await account_rows(session, now) if r["id"] == str(account_id)]
    acct = rows[0]
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
                "SELECT text, cited_source_codes FROM report_sections WHERE account_id = :a AND"
                " kind = 'narrative'"
                " AND visibility = 'public' ORDER BY version DESC, created_at DESC, position LIMIT"
                " 1"
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
                    "source_url": found[codes[i]].url
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
            "source_url": r.url,
            "source_code": r.source_ref,
        }

    facts_sql = (
        "SELECT f.id, coalesce(f.value_text, f.value_json->>'text') AS v, f.product_grade,"
        " f.attribute_key, f.lane,"
        " f.flags, f.run_id, f.created_at, e.url, e.evidence_date, e.source_ref"
        " FROM facts f LEFT JOIN LATERAL (SELECT url, evidence_date, source_ref FROM fact_evidence"
        " x"
        "   WHERE x.fact_id = f.id ORDER BY evidence_date DESC NULLS LAST LIMIT 1) e ON true"
        " WHERE f.account_id = :a AND f.status = 'current' AND f.superseded_at IS NULL AND"
        " f.visibility = 'public'"
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
        changed_lanes = {f.lane for f in moved if f.lane}
        all_lanes = (
            (await session.execute(text("SELECT DISTINCT unnest(lanes) AS lane FROM accounts")))
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
                "SELECT"
                " (SELECT count(*) FROM signal_events WHERE account_id = :a) AS signals,"
                " ((SELECT count(*) FROM people WHERE account_id = :a AND NOT excluded AND"
                " merged_into IS NULL"
                "   AND erased_at IS NULL AND NOT coalesce(internal_only, false))"
                f"  + (SELECT count(*) FROM facts f WHERE {FACT_CLASS['people']})) AS people,"
                " ((SELECT count(*) FROM job_postings WHERE account_id = :a AND status <> 'closed')"
                f"  + (SELECT count(*) FROM facts f WHERE {FACT_CLASS['jobs']})) AS jobs,"
                " ((SELECT count(*) FROM tech_detections WHERE account_id = :a)"
                f"  + (SELECT count(*) FROM facts f WHERE {FACT_CLASS['tech']})) AS tech,"
                f" (SELECT count(*) FROM facts f WHERE {FACT_CLASS['news']}) AS news,"
                " (SELECT count(*) FROM connector_items WHERE account_id = :a AND owner_user_id ="
                " :u"
                "   AND state = 'matched') AS mail_drive,"
                " ((SELECT count(*) FROM sources WHERE account_id = :a)"
                "  + (SELECT count(DISTINCT t.response->'args'->>'url') FROM tool_calls t"
                "   JOIN account_runs r ON r.id = t.run_id WHERE r.account_id = :a"
                "   AND t.tool IN ('p_search:read_page', 'p_scrape:scrape_job_page')"
                "   AND t.response->'args'->>'url' IS NOT NULL AND NOT EXISTS (SELECT 1 FROM"
                "   sources x WHERE x.account_id = :a AND x.url = t.response->'args'->>'url')))"
                " AS sources,"
                " (SELECT count(*) FROM tool_calls t JOIN account_runs r ON r.id = t.run_id"
                "   WHERE r.account_id = :a AND t.tool = 'p_search:web_search') AS searches,"
                " (SELECT count(*) FILTER (WHERE c.status = 'done') FROM coverage_rows c WHERE"
                "   c.run_id = (SELECT r.id FROM account_runs r WHERE r.account_id = :a"
                "   AND r.status IN ('COMPLETED', 'PARTIAL', 'BUDGET_EXCEEDED')"
                "   AND EXISTS (SELECT 1 FROM coverage_rows x WHERE x.run_id = r.id)"
                "   ORDER BY r.finished_at DESC NULLS LAST LIMIT 1)) AS coverage_done,"
                " (SELECT count(*) FROM coverage_rows c WHERE"
                "   c.run_id = (SELECT r.id FROM account_runs r WHERE r.account_id = :a"
                "   AND r.status IN ('COMPLETED', 'PARTIAL', 'BUDGET_EXCEEDED')"
                "   AND EXISTS (SELECT 1 FROM coverage_rows x WHERE x.run_id = r.id)"
                "   ORDER BY r.finished_at DESC NULLS LAST LIMIT 1)) AS coverage_total"
            ),
            {"a": account_id, "u": user_id},
        )
    ).one()

    files = await deliverables(session, account_id)
    job = (
        await session.execute(
            text(
                "SELECT j.id, j.kind, j.scope, j.status, j.topic,"
                " (SELECT count(*) FROM coverage_rows c WHERE c.run_id = j.run_id) AS total,"
                " (SELECT count(*) FROM coverage_rows c WHERE c.run_id = j.run_id"
                "   AND c.status IS NOT NULL) AS done"
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
        },
        "stopped_run": stopped,
    }


# The research graph records what it finds as graded facts keyed ``<class>.<step>.<attribute>``
# (it does not fill the people, job_postings and tech_detections tables of the older workflow),
# so each deep-dive category also counts and lists the public facts of its class (audit
# 2026-10-15: every category showed 0 after a finished research).
_FACT_PUBLIC = "f.account_id = :a AND f.status = 'current' AND f.visibility = 'public'"
FACT_CLASS = {
    "people": f"{_FACT_PUBLIC} AND f.attribute_key LIKE 'people.%'",
    "jobs": f"{_FACT_PUBLIC} AND f.attribute_key LIKE 'jobs.%'",
    "tech": f"{_FACT_PUBLIC} AND f.attribute_key LIKE 'tech.%'",
    "news": (
        f"{_FACT_PUBLIC} AND (f.attribute_key LIKE 'news.%' OR split_part(f.attribute_key, '.', 2)"
        " IN ('company_news', 'leadership_changes', 'competitor_moves',"
        " 'regulatory_quality_events', 'events_sponsored'))"
    ),
}

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
    "claims_dropped": "what was found did not pass checking",
}


def gap_text(code: str) -> str:
    """One readable reason for a step that could not finish."""
    return GAP_TEXT.get(code, code.replace("_", " "))


async def coverage_gaps(session: Any, account_id: UUID) -> list[dict[str, Any]]:
    """The steps of the latest finished research that could not be finished, by reason."""
    rows = (
        await session.execute(
            text(
                "SELECT coalesce(c.reason_code, 'unknown') AS code, count(*) AS n"
                " FROM coverage_rows c WHERE c.run_id = (SELECT r.id FROM account_runs r"
                "   WHERE r.account_id = :a AND r.status IN ('COMPLETED', 'PARTIAL',"
                "   'BUDGET_EXCEEDED') AND EXISTS (SELECT 1 FROM coverage_rows x WHERE"
                "   x.run_id = r.id) ORDER BY r.finished_at DESC NULLS LAST LIMIT 1)"
                " AND c.status IS DISTINCT FROM 'done' AND c.status IS DISTINCT FROM 'none'"
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
    "BadRequestError": (
        "The AI model provider refused the request, often because its usage limit was reached."
        " Restore it, then resume."
    ),
    "gate_failed": (
        "Some steps could not be finished, so the quality check held the files back."
        " Open Coverage to see which steps and why."
    ),
    "steps_waiting": "Some steps could not be finished. Open Coverage to see which steps and why.",
    "pull_only": "This run only collected data; nothing was written up yet.",
    "slice_ceiling": "It ran longer than the platform allows for one research.",
    "budget_cap": "It reached the credits confirmed for it.",
}
# "build_failed:<why>": the files could not be built from what was found (audit 2026-10-15)
BUILD_FAILED_TEXT = "The files could not be built from what was found ({why})."


def stop_text(reason: str) -> str:
    """What the user is told about a research that stopped: a known reason in words, a build
    failure with its cause, anything else with its code so it can be reported."""
    if reason in STOP_TEXT:
        return STOP_TEXT[reason]
    if reason.startswith("build_failed"):
        why = reason.partition(":")[2].replace("_", " ").replace(",", ", ") or "no detail"
        return BUILD_FAILED_TEXT.format(why=why)
    return f"It stopped before finishing (reason: {reason.replace('_', ' ')})."


async def stopped_run(session: Any, account_id: UUID) -> dict[str, Any] | None:
    """The account's latest research when it stopped before finishing (PARTIAL or FAILED with a
    reason other than a cancel) or stalled, with what it holds: the screen offers Resume research
    (owner decision 2026-10-14). None when the latest research finished or is moving."""
    row = (
        await session.execute(
            text(
                "SELECT r.id, r.status, r.finished_reason, r.stalled_at,"
                " (SELECT count(*) FROM coverage_rows c WHERE c.run_id = r.id) AS total,"
                " (SELECT count(*) FROM coverage_rows c WHERE c.run_id = r.id"
                "   AND c.status IN ('done', 'none')) AS done"
                " FROM account_runs r WHERE r.account_id = :a AND coalesce(r.input_mode,"
                " 'company') = 'company' ORDER BY r.created_at DESC LIMIT 1"
            ),
            {"a": account_id},
        )
    ).first()
    if row is None:
        return None
    stalled = row.stalled_at is not None and row.status not in (
        "COMPLETED", "PARTIAL", "FAILED", "CANCELED", "BUDGET_EXCEEDED"
    )  # fmt: skip
    ended_early = row.status in ("PARTIAL", "FAILED", "BUDGET_EXCEEDED")
    # a research that delivered its files with steps it could not finish (consent, waiting on
    # the owner, not approved) is finished, not stopped: Coverage says which steps and why
    stopped = ended_early and row.finished_reason not in (
        None, "completed", "canceled", "user_canceled", "steps_waiting"
    )  # fmt: skip
    if not (stalled or stopped):
        return None
    reason = "stalled" if stalled else str(row.finished_reason)
    return {
        "run_id": str(row.id),
        "stalled": stalled,
        "reason": stop_text(reason),
        "steps_done": int(row.done),
        "steps_total": int(row.total),
    }


async def deliverables(session: Any, account_id: UUID) -> list[dict[str, Any]]:
    """The newest version of each deliverable of the account, in the design's order."""
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT ON (kind) id, kind, filename, size_bytes, built_at FROM"
                " deliverables"
                " WHERE account_id = :a AND kind = ANY(:k) ORDER BY kind, version DESC"
            ),
            {"a": account_id, "k": DELIVERABLE_ORDER},
        )
    ).all()
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
