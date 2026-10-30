"""The statements of ``replica.credits``, copied from the module so that a test fails when one of
them changes (``tests/replica/test_mutation_kills_credits_ledger.py`` answers a scripted session by
these exact texts). Regenerate by reading the ``text(...)`` calls of the module; nothing here is
imported by the product."""

from __future__ import annotations

CAP_PER_PULL = "SELECT credit_cap_per_pull FROM platform_defaults WHERE id = 1"

POOL = (
    "SELECT coalesce(sum(credits) FILTER (WHERE kind = 'reserve'), 0) AS reserved, "
    "coalesce(sum(credits) FILTER (WHERE kind = 'release'), 0) AS released, "
    "coalesce(sum(credits) FILTER (WHERE kind = 'settle'), 0) AS used FROM "
    "credit_entries WHERE period = :p"
)

PAST_JOBS = (
    "SELECT credits_used FROM research_jobs WHERE account_id = :a AND scope = :s AND "
    "status = 'done' AND credits_used > 0 ORDER BY finished_at DESC LIMIT :n"
)

OPEN_JOB = (
    "SELECT id, kind, scope, status, credits_estimated, created_at, topic FROM "
    "research_jobs WHERE account_id = :a AND status IN ('waiting_credit_limit', "
    "'queued', 'running') ORDER BY created_at DESC LIMIT 1"
)

HAS_REQUEST = "SELECT 1 FROM research_jobs WHERE request_id = :r"

LOCK = "SELECT 1 FROM client_settings FOR UPDATE"

INSERT_JOB = (
    "INSERT INTO research_jobs (account_id, kind, scope, status, period, "
    "credits_estimated, requested_by, request_id, from_turn_id, created_at, topic, "
    "models, resume_from, goal) VALUES (:a, :k, :s, :st, :p, :c, :by, :r, :t, :now, "
    ":topic, CAST(:models AS jsonb), :resume, :goal) RETURNING id"
)

PRIOR = "SELECT id, status FROM research_jobs WHERE request_id = :r"

ENTRY = (
    "INSERT INTO credit_entries (period, kind, credits, job_id, purpose, actor, at) "
    "VALUES (:p, :k, :c, :j, :pu, :a, :now) ON CONFLICT (tenant_id, job_id, kind) DO "
    "NOTHING"
)

UPDATE_JOB = (
    "UPDATE research_jobs SET status = :st, credits_used = :u, finished_at = :now, "
    "failure_reason = :r, stop_reason = :sr WHERE id = :j"
)

SELECT_JOB = (
    "SELECT id, kind, period, credits_estimated, status, requested_by FROM research_jobs "
    "WHERE id = :j FOR UPDATE"
)

LOCK2 = "SELECT 1 FROM client_settings FOR UPDATE"

QUEUE = "UPDATE research_jobs SET status = 'queued', period = :p WHERE id = :j"

WAITING = (
    "SELECT id, kind, credits_estimated, requested_by FROM research_jobs WHERE status = "
    "'waiting_credit_limit' ORDER BY created_at"
)

CLAIM = (
    "UPDATE research_jobs SET status = 'running', started_at = :now WHERE id = :j AND "
    "status = 'queued' RETURNING id, account_id, kind, scope, credits_estimated, "
    "requested_by, topic, models, resume_from, goal"
)

REQUEUE = (
    "UPDATE research_jobs SET status = 'queued' WHERE id IN ( SELECT id FROM "
    "research_jobs WHERE status = 'running' AND run_id IS NULL AND coalesce(started_at, "
    "created_at) < :b ORDER BY created_at LIMIT :n) RETURNING id"
)

LINK_RUN = "UPDATE research_jobs SET run_id = :r WHERE id = :j"

START_WITH_RUN = (
    "UPDATE research_jobs SET run_id = :r, status = 'running', started_at = "
    "coalesce(started_at, :now) WHERE id = :j AND status IN ('queued', 'running') "
    "RETURNING id"
)

JOB_OF_RUN = (
    "SELECT id, kind, account_id, requested_by, status, credits_estimated, credits_used "
    "FROM research_jobs WHERE run_id = :r"
)

LIBRARY_ADD = (
    "INSERT INTO research_jobs (account_id, kind, scope, status, period, "
    "credits_estimated, credits_used, requested_by, created_at, started_at, finished_at) "
    "VALUES (:a, 'library_add', 'library', 'done', :p, 0, 0, :by, :now, :now, :now) "
    "RETURNING id"
)

LEDGER = (
    "SELECT j.id, j.kind, j.status, j.credits_estimated, j.credits_used, "
    "coalesce(sum(e.credits) FILTER (WHERE e.kind = 'reserve'), -1) AS reserved, "
    "coalesce(sum(e.credits) FILTER (WHERE e.kind = 'release'), -1) AS released, "
    "coalesce(sum(e.credits) FILTER (WHERE e.kind = 'settle'), 0) AS settled FROM "
    "research_jobs j LEFT JOIN credit_entries e ON e.job_id = j.id GROUP BY j.id ORDER "
    "BY j.id"
)

HELD = (
    "SELECT period, coalesce(sum(credits) FILTER (WHERE kind = 'reserve'), 0) - "
    "coalesce(sum(credits) FILTER (WHERE kind = 'release'), 0) AS held FROM "
    "credit_entries GROUP BY period"
)

ACCOUNT_LABEL = "SELECT domain, coalesce(name, domain) AS name FROM accounts WHERE id = :a"

LAST_FINISHED = (
    "SELECT max(f) FROM (SELECT finished_at AS f FROM account_runs WHERE account_id = :a "
    "AND status IN ('COMPLETED', 'PARTIAL') UNION ALL SELECT finished_at FROM "
    "research_jobs WHERE account_id = :a AND status = 'done') x"
)
