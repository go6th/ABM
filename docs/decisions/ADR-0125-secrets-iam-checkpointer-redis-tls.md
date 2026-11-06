# ADR-0125: the checkpointer signs in with IAM, Redis checks the hostname, sign-in survives a Redis outage, the audit archive is locked

- Status: Accepted (code); Terraform planned, NOT applied
- Date: 2026-11-03
- Deciders: owner (fix prompt D, task 4)

## (a) The LangGraph checkpointer

`CHECKPOINT_DATABASE_URL` was a password-bearing psycopg URL in Secret Manager, given to the api,
the worker and the jobs, while the main engine signs in with Cloud SQL IAM through the Cloud SQL
Python connector. The connector serves asyncpg and pg8000 only (no psycopg), and LangGraph's
`AsyncPostgresSaver` needs a psycopg `AsyncConnection`, so the connector cannot be used.

Decision: in staging and production the saver connects to the instance's **private IP**
(`CLOUD_SQL_PRIVATE_IP`, Terraform's new `module.cloud_sql.private_ip` output) as the service's IAM
database user (`DB_IAM_USER`) with a **short-lived OAuth access token** (scope
`sqlservice.login`, cached until five minutes before expiry, refreshed off the event loop) as the
password: manual IAM database authentication. TLS is `sslmode=require` (the instance is
`ENCRYPTED_ONLY`; as before), or `verify-ca` when `CLOUD_SQL_CA_FILE` names the server CA (not
wired in Terraform yet: a follow-up). The local profiles keep `CHECKPOINT_DATABASE_URL`; a cloud
release that still carries it uses it. Only the processes that run slices need a checkpointer
(`needs_checkpointer`: worker, job, all); **the api is no longer given the URL at all**, which
also removes the password URL from the api process (it would have undercut ADR-0123).

Terraform (plan): `CHECKPOINT_DATABASE_URL` leaves the api, worker, spend-invariants and
reingest-tables; `CLOUD_SQL_PRIVATE_IP` is added to the worker and the two jobs; the secret
`checkpoint-database-url` loses all three readers but is **kept** one release so a rollback can
re-grant it (delete it once the IAM checkpointer has run a research on staging).
VERIFY(V-SQL-2): manual IAM login over the private IP; check on staging that a research slice
writes checkpoints (Logs: no `checkpoint` connection errors on the worker).

## (b) Redis TLS

`REDIS_URL` pinned the Memorystore CA but set `ssl_check_hostname=false`. Google's own client
sample dials the instance IP with only the CA pool (Go's TLS then checks the IP against the
certificate), so the server certificate is issued for the instance IP. Decision: Terraform sets
`ssl_check_hostname=true`, and, whatever the hostname check says, a compensating check in code
(`platform/redis_url.py`, the one builder every Redis client now goes through): in the cloud the
URL must be `rediss://`, pin `ssl_ca_certs`, not relax `ssl_cert_reqs`, and name a private IP
literal equal to `REDIS_PINNED_HOST` (new; Terraform sets it from `module.cache.host`). VERIFY(V-REDIS-1)
**before applying**: from a VM in the VPC, `openssl s_client -connect 10.240.58.3:6378 -CAfile
ca.pem -verify_ip 10.240.58.3` must say `Verify return code: 0`. If it does not, set
`ssl_check_hostname=false` back in `infra/stack/main.tf`: the code check still pins the host.

## (c) Sign-in when Redis is down

The sign-in buckets (`login`, `sso_discover`) now fail **open** when Redis cannot be reached: the
attempt goes through this instance's own bounded in-process bucket (so a burst on one instance is
still refused), `rate_limit_degraded` is logged (`kind` = action, `error_class`),
`auth_rate_limit_degraded_total` is counted (`config/telemetry/metrics.yaml`,
`identity/obs.py`), and the observability module gets a log-based metric and a **page** alert on
the event (runbook `sso-outage.md`). Cloud Armor's per-IP sign-in rule (30 a minute) still holds
at the edge. Every other bucket (enroll, resend, verify, password reset, invite accept, the
per-user buckets) stays fail **closed** (503).

## (d) The audit-archive retention lock

`infra/envs/staging/main.tf`: `archive_lock` now defaults to `true`; the plan shows
`retention_policy.is_locked = false -> true` on `ai-staging-audit-archive`.

**APPLYING THIS CAN NEVER BE UNDONE.** Once locked, the 7-year retention policy cannot be removed
or shortened, the bucket cannot be deleted until every object in it is 7 years old, and the
project cannot be deleted while the bucket holds objects. The owner must apply it deliberately,
on its own if possible, after reading that plan line. To keep staging unlocked, set
`archive_lock = false` in `terraform.tfvars` before any apply.
