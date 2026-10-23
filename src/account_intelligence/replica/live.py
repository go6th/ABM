"""The Research screen, live: every web research of the client that is running, and the ones that
finished recently, with where each one is and what it has found so far (owner request
2026-10-21: one page, Start and Stop, data on screen within a second of being stored).

Everything is read from the database on each call, inside the caller's tenant transaction (forced
RLS); nothing is cached and the browser stores nothing. The stream (``api.routes.live``) calls
``snapshot`` once a second and sends it only when it changed, so a fact the worker saves is on
screen within about a second.

* ``running``: the client's open research jobs (waiting, queued, running), oldest first.
* ``recent``: the ones that ended in the last ``recent_days``, newest first.
* each item: the company, the kind (new research, refresh), its step ("Step 3 of 8: Reading and
  grading"), and counts so far: findings (current public facts), searches (finished source
  calls) and model tokens.
* ``detail``: the selected research (or the first running one): its steps, its findings by kind,
  and its newest findings, each with the host it was read on. A finding whose text the client
  output scan refuses is left out and counted, never shown.

Only web research is listed (new research and refreshes), never file analysis or library adds.

GUARANTEE(test_live_snapshot_shows_a_fact_as_soon_as_it_is_stored)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import text

from account_intelligence.replica.workspace import PLANNED, can_resume, resume_context
from account_intelligence.safety.client_output import assert_client_safe, scan_text
from views import progress as P

WEB_KINDS = ("new_research", "web_refresh", "scheduled_refresh")
OPEN = ("waiting_credit_limit", "queued", "running")
KIND_LABELS: Mapping[str, str] = {
    "new_research": "New research",
    "web_refresh": "Refresh from web",
    "scheduled_refresh": "Scheduled refresh",
}
SCOPE_LABELS: Mapping[str, str] = {
    "full": "Everything",
    "jobs": "Job postings",
    "people": "People and teams",
    "topic": "One topic",
}
JOB_LABELS: Mapping[str, str] = {
    "waiting_credit_limit": "Waiting for this month's limit",
    "queued": "Starting soon",
    "running": "Starting",
    "done": "Complete",
    "failed": "Stopped",
    "canceled": "Cancelled",
}
# what a finding is about, from the first part of its key (agents.production.provider
# attribute_prefix_by_row; a key without one starts with its checklist row)
FINDING_KINDS: Mapping[str, str] = {
    "jobs": "Job postings",
    "hiring": "Job postings",
    "people": "People",
    "exec": "Leadership",
    "tech": "Technology",
    "funding": "Financials",
    "company": "News and company",
}


@dataclass(frozen=True)
class LiveSettings:
    """``config/views.yaml`` ``live``."""

    poll_s: float = 1.0
    max_duration_s: float = 60.0
    heartbeat_s: float = 15.0
    # with nothing running the stream sends one snapshot and ends; the browser asks again after
    # this long (an open stream keeps an instance awake, and there is nothing to follow)
    idle_retry_s: float = 5.0
    recent_days: int = 14
    recent_limit: int = 20
    latest_findings: int = 25


def load_settings() -> LiveSettings:
    """Read the ``live`` section (each call: a changed file applies to the next stream)."""
    import yaml

    from account_intelligence.config import CONFIG_DIR

    raw = yaml.safe_load((CONFIG_DIR / "views.yaml").read_text()).get("live") or {}
    base = LiveSettings()
    return LiveSettings(
        poll_s=float(raw.get("poll_s", base.poll_s)),
        max_duration_s=float(raw.get("max_duration_s", base.max_duration_s)),
        heartbeat_s=float(raw.get("heartbeat_s", base.heartbeat_s)),
        idle_retry_s=float(raw.get("idle_retry_s", base.idle_retry_s)),
        recent_days=int(raw.get("recent_days", base.recent_days)),
        recent_limit=int(raw.get("recent_limit", base.recent_limit)),
        latest_findings=int(raw.get("latest_findings", base.latest_findings)),
    )


def finding_kind(attribute_key: str) -> str:
    """The kind of a finding, from its key (``jobs.job_postings_12_months.title`` is Job
    postings); a key with no known prefix is "Other"."""
    return FINDING_KINDS.get(attribute_key.split(".", 1)[0], "Other")


def finding_label(attribute_key: str, labels: Mapping[str, str]) -> str:
    """The checklist row the finding answers, in words (its label), else the key's words."""
    parts = attribute_key.split(".")
    for part in parts[:2]:
        if part in labels:
            return labels[part]
    return " ".join(parts[-1].split("_")).capitalize()


def _host(url: str | None) -> str | None:
    host = (urlsplit(url or "").hostname or "").lower().removeprefix("www.")
    return host or None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def _jobs(s: Any, cfg: LiveSettings) -> list[Any]:
    rows = (
        await s.execute(
            text(
                "SELECT j.id, j.kind, j.scope, j.status, j.topic, j.run_id, j.created_at,"
                " j.started_at, j.finished_at, j.stop_reason, j.failure_reason,"
                " a.domain, a.name, r.status AS run_status, r.finished_reason, r.stop_detail,"
                " r.stalled_at, " + resume_context("r") + ","
                " (r.run_mode IN ('pull', 'build') OR (r.input_mode = 'company'"
                "  AND (r.run_input ? 'job_id' OR r.run_input ? 'linked_file_run'))) AS abm"
                " FROM research_jobs j JOIN accounts a ON a.id = j.account_id"
                " LEFT JOIN account_runs r ON r.id = j.run_id"
                " WHERE j.kind = ANY(:kinds)"
                " AND (j.status = ANY(:open) OR j.finished_at > now() - make_interval(days => :d))"
                " ORDER BY (j.status = ANY(:open)) DESC, j.created_at DESC LIMIT :n"
            ),
            {
                "kinds": list(WEB_KINDS),
                "open": list(OPEN),
                "d": cfg.recent_days,
                "n": cfg.recent_limit + 50,
            },
        )
    ).all()
    return list(rows)


async def _counts(s: Any, run_ids: Sequence[UUID]) -> dict[UUID, dict[str, int]]:
    """Findings, searches and model tokens per run (each by run id; indexed)."""
    out: dict[UUID, dict[str, int]] = {
        r: {"findings": 0, "searches": 0, "tokens": 0} for r in run_ids
    }
    if not run_ids:
        return out
    ids = list(run_ids)
    for row in (
        await s.execute(
            text(
                "SELECT run_id, count(*) AS n FROM facts WHERE run_id = ANY(:ids)"
                " AND superseded_at IS NULL AND status = 'current' AND visibility = 'public'"
                " GROUP BY run_id"
            ),
            {"ids": ids},
        )
    ).all():
        out[row.run_id]["findings"] = int(row.n)
    for row in (
        await s.execute(
            text(
                "SELECT run_id, count(*) AS n FROM tool_calls WHERE run_id = ANY(:ids)"
                " AND response_ref IS NOT NULL GROUP BY run_id"
            ),
            {"ids": ids},
        )
    ).all():
        out[row.run_id]["searches"] = int(row.n)
    # model tokens: the router's ledger (llm_calls) and the research graph's reply log
    # (model_reply_log, written when the router is off) each hold a run's calls; the larger is
    # the run's use (the two never both hold the same run's calls in full)
    for table, extra in (("llm_calls", ""), ("model_reply_log", " AND served_from = 'model'")):
        for row in (
            await s.execute(
                text(
                    f"SELECT run_id, COALESCE(sum(input_tokens + output_tokens), 0) AS n"  # noqa: S608 - fixed names
                    f" FROM {table} WHERE run_id = ANY(:ids){extra} GROUP BY run_id"
                ),
                {"ids": ids},
            )
        ).all():
            out[row.run_id]["tokens"] = max(out[row.run_id]["tokens"], int(row.n))
    return out


async def _nodes(s: Any, tenant_id: UUID, run_ids: Sequence[UUID]) -> dict[UUID, set[str]]:
    """The research graph's finished nodes per run (latest checkpoint; RLS keeps the tenant)."""
    if not run_ids:
        return {}
    threads = {f"{tenant_id}:{r}": r for r in run_ids}
    rows = (
        await s.execute(
            text(
                "SELECT DISTINCT ON (thread_id) thread_id,"
                " array(SELECT jsonb_object_keys(checkpoint -> 'versions_seen')) AS seen"
                " FROM checkpoints WHERE thread_id = ANY(:t) AND checkpoint_ns = ''"
                " ORDER BY thread_id, checkpoint_id DESC"
            ),
            {"t": list(threads)},
        )
    ).all()
    return {threads[r.thread_id]: set(r.seen or ()) & P.ABM_NODES for r in rows}


def _progress(
    row: Any,
    nodes: set[str] | None,
    stages: Sequence[tuple[str, str]],
    owners: Mapping[str, str],
    coverage: Sequence[tuple[str, str | None]],
) -> P.Progress:
    abm = bool(row.abm) or bool((nodes or set()) & P.ABM_ONLY)
    return P.compute(
        str(row.run_id),
        str(row.run_status),
        stages,
        owners,
        coverage,
        nodes_done=(nodes or set()) if abm else None,
    )


def _item(row: Any, prog: P.Progress | None, counts: Mapping[str, int]) -> dict[str, Any]:
    open_ = row.status in OPEN
    at = None
    if prog is not None and not prog.finished:
        steps = list(prog.steps)
        for i, step in enumerate(steps):
            if step.state in ("running", "paused"):
                at = {"index": i + 1, "total": len(steps), "name": step.name}
                break
    label = prog.label if prog is not None else JOB_LABELS.get(row.status, "Waiting")
    if row.status in ("waiting_credit_limit", "queued"):
        label = JOB_LABELS[row.status]
    return {
        "job_id": str(row.id),
        "run_id": str(row.run_id) if row.run_id else None,
        "company": row.name or row.domain,
        "domain": row.domain,
        "kind": KIND_LABELS.get(row.kind, "Research"),
        "scope": SCOPE_LABELS.get(row.scope, "Everything"),
        "topic": row.topic,
        "status": row.status,
        "run_status": row.run_status,
        "label": label,
        "open": open_,
        "can_stop": open_,
        # the account page's rule (``workspace.can_resume``) on the job's run
        "can_resume": row.run_id is not None
        and can_resume(
            row.run_status, row.finished_reason, row.stalled_at, row.is_latest, row.job_open
        ),
        "step": at,
        "steps_done": prog.stages_done if prog is not None else 0,
        "steps_total": prog.stages_total if prog is not None else len(P.ABM_STEPS),
        "findings": counts.get("findings", 0),
        "searches": counts.get("searches", 0),
        "tokens": counts.get("tokens", 0),
        "created_at": _iso(row.created_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
    }


async def _detail(
    s: Any,
    row: Any,
    prog: P.Progress | None,
    counts: Mapping[str, int],
    cfg: LiveSettings,
    labels: Mapping[str, str],
) -> dict[str, Any]:
    run_id = row.run_id
    by_kind: dict[str, int] = {}
    latest: list[dict[str, Any]] = []
    withheld = 0
    pages = 0
    if run_id is not None:
        for f in (
            await s.execute(
                text(
                    "SELECT attribute_key FROM facts WHERE run_id = :r AND superseded_at IS NULL"
                    " AND status = 'current' AND visibility = 'public'"
                ),
                {"r": run_id},
            )
        ).all():
            kind = finding_kind(f.attribute_key)
            by_kind[kind] = by_kind.get(kind, 0) + 1
        for f in (
            await s.execute(
                text(
                    "SELECT f.id, f.attribute_key, f.value_text, f.product_grade, f.created_at,"
                    " (SELECT e.url FROM fact_evidence e WHERE e.fact_id = f.id"
                    "  ORDER BY e.created_at, e.id LIMIT 1) AS url"
                    " FROM facts f WHERE f.run_id = :r AND f.superseded_at IS NULL"
                    " AND f.status = 'current' AND f.visibility = 'public'"
                    " ORDER BY f.created_at DESC, f.id LIMIT :n"
                ),
                {"r": run_id, "n": cfg.latest_findings},
            )
        ).all():
            value = (f.value_text or "").strip()
            label = finding_label(f.attribute_key, labels)
            if not value or scan_text(value) or scan_text(label):
                withheld += 1
                continue
            latest.append(
                {
                    "fact_id": str(f.id),
                    "kind": finding_kind(f.attribute_key),
                    "label": label,
                    "value": value[:400],
                    "grade": f.product_grade,
                    "source": _host(f.url),
                    "found_at": _iso(f.created_at),
                }
            )
        pages = int(
            await s.scalar(
                text(
                    "SELECT count(*) FROM tool_calls WHERE run_id = :r AND response_ref IS NOT"
                    " NULL AND operation IN ('read_page', 'scrape_job_page')"
                ),
                {"r": run_id},
            )
            or 0
        )
    reason = None
    if row.finished_reason == "canceled_by_user":
        reason = "Stopped by you."
    elif row.status in ("failed", "canceled") or (row.run_status in ("FAILED", "CANCELED")):
        reason = row.stop_reason or row.failure_reason
    return {
        "job_id": str(row.id),
        "run_id": str(run_id) if run_id else None,
        "company": row.name or row.domain,
        "domain": row.domain,
        "label": prog.label if prog is not None else JOB_LABELS.get(row.status, "Waiting"),
        "open": row.status in OPEN,
        "steps": [
            {
                "id": st.id,
                "name": st.name,
                "state": st.state,
                "rows_done": st.rows_done,
                "rows_total": st.rows_total,
            }
            for st in (prog.steps if prog is not None else ())
        ],
        "findings": counts.get("findings", 0),
        "searches": counts.get("searches", 0),
        "pages_read": pages,
        "tokens": counts.get("tokens", 0),
        "findings_by_kind": [{"kind": k, "count": n} for k, n in sorted(by_kind.items())],
        "latest_findings": latest,
        "withheld": withheld,
        "reason": reason if reason and not scan_text(reason) else None,
    }


async def snapshot(
    s: Any,
    tenant_id: UUID,
    *,
    stages: Sequence[tuple[str, str]],
    owners: Mapping[str, str],
    labels: Mapping[str, str],
    selected: UUID | None,
    cfg: LiveSettings,
    now: datetime,
) -> dict[str, Any]:
    """The Research screen's data (see the module docstring), client-safe."""
    jobs = await _jobs(s, cfg)
    open_jobs = [j for j in jobs if j.status in OPEN]
    recent = [j for j in jobs if j.status not in OPEN][: cfg.recent_limit]
    shown = [*open_jobs, *recent]
    run_ids = [j.run_id for j in shown if j.run_id is not None]
    counts = await _counts(s, run_ids)
    live_runs = [j.run_id for j in open_jobs if j.run_id is not None]
    pick = next((j for j in shown if selected is not None and selected in (j.id, j.run_id)), None)
    if pick is None and selected is None:
        pick = open_jobs[0] if open_jobs else (recent[0] if recent else None)
    wanted = [*live_runs, *([pick.run_id] if pick is not None and pick.run_id else [])]
    nodes = await _nodes(s, tenant_id, list(dict.fromkeys(wanted)))
    coverage: dict[UUID, list[tuple[str, str | None]]] = {}
    if run_ids:
        for c in (
            await s.execute(
                text(
                    # the rows in the research's scope (replica.workspace.PLANNED): a scoped
                    # one marks the rest "not planned" at once, and they used to count as
                    # reported rows ("33 of 33" for a 3-row research, owner report 2026-10-21)
                    "SELECT c.run_id, c.row_key, c.status FROM coverage_rows c"
                    f" WHERE c.run_id = ANY(:ids) AND {PLANNED}"
                ),
                {"ids": run_ids},
            )
        ).all():
            coverage.setdefault(c.run_id, []).append((c.row_key, c.status))
    progs: dict[UUID, P.Progress] = {}
    for j in shown:
        if j.run_id is not None and j.run_status is not None:
            progs[j.id] = _progress(
                j, nodes.get(j.run_id), stages, owners, coverage.get(j.run_id, [])
            )
    body = {
        "as_of": now.isoformat(),
        "running": [_item(j, progs.get(j.id), counts.get(j.run_id, {})) for j in open_jobs],
        "recent": [_item(j, progs.get(j.id), counts.get(j.run_id, {})) for j in recent],
        "detail": (
            await _detail(s, pick, progs.get(pick.id), counts.get(pick.run_id, {}), cfg, labels)
            if pick is not None
            else None
        ),
    }
    assert_client_safe([json.dumps({k: v for k, v in body.items() if k != "as_of"}, default=str)])
    return body


def version(body: Mapping[str, Any]) -> str:
    """A strong tag of the snapshot's content (its time left out): the stream sends a snapshot
    only when this changes."""
    data = json.dumps({k: v for k, v in body.items() if k != "as_of"}, sort_keys=True, default=str)
    return '"' + hashlib.sha256(data.encode()).hexdigest()[:32] + '"'
