# Agent 1 "security-audit": work log

Date: 2026-10-14. Read-only audit of `/home/sakib/abm-replica` (branch `feat/replica`, clean
working tree, migration head 0068). Report: `docs/deploy/SECURITY_AUDIT.md`.

Files written: this file and `docs/deploy/SECURITY_AUDIT.md`. No other file in the repository
was changed, and no git command that writes was run. Scratch outputs were kept outside the
repository, in the session scratchpad (`.../scratchpad/secaudit/`). No `.env*` file and no
`web/.env.local` was opened. No secret value was printed: secret-scan hits were classified by
length, entropy and word content only.

## What I ran (commands, result counts)

| # | Check | Command (abridged where long) | Result |
| --- | --- | --- | --- |
| 1 | RLS catalog (replica DB, read-only) | `docker exec -i abm-replica-db-1 psql -U postgres -d ai -v ON_ERROR_STOP=1 -tA -f - < .claude/skills/account-intelligence-platform/scripts/check_rls_catalog.sql` | exit 0, **0 rows** |
| 2 | Owner rule (0063) on every owned table (replica DB, read-only SELECT on `pg_class`, `pg_attribute`, `pg_policy`) | query in scratch `owner_rule.sql` | 108 owned tables: all forced RLS, NOT NULL owner, `app_rw` policy with `owner_user_id` in USING and WITH CHECK; 6 other-role policies (operator_resolver, public_reader, pilot_reader) |
| 3 | Roles and SECURITY DEFINER functions (replica DB, read-only) | `SELECT ... FROM pg_roles`, `SELECT ... FROM pg_proc WHERE prosecdef` | app_rw NOSUPERUSER NOBYPASSRLS; 38 definer functions, of which 12 are owned by `postgres` (LOGIN, superuser): see M1 |
| 4 | Route catalog | `uv run --frozen python scripts/check_route_catalog.py` | "every route has a policy, an audit class and a session tenant"; route dump from OpenAPI: 218 operations (100 GET, 98 POST, 14 PUT, 5 DELETE, 1 PATCH) |
| 5 | Isolation suites (own test DB) | `PGPASSWORD=dev PGHOST=localhost PGPORT=5433 TEST_DB=ai_agent_security_test uv run --frozen pytest -p no:cacheprovider tests/isolation/test_matrix.py tests/isolation/test_ownership.py -q -x` | **688 passed** in 131.9 s; the fixture dropped the test DB afterwards (0 left) |
| 6 | Dynamic SQL scan | AST scan (scratch `sqlscan.py`) over `src/` for `text`/`execute`/`exec_driver_sql` with a non-constant first argument | 53 sites, 34 annotated `nosec`/`noqa: S608`, 19 not annotated (constant strings in variables or allow-listed builders), 0 unsafe |
| 7 | Secret scan, git history | `docker run --rm -v "/home/sakib/ABM Generator/.git":/repo.git:ro -e GIT_CONFIG_*=safe.directory zricethezav/gitleaks:v8.21.2 detect --source /repo.git --redact --report-format json` | 438 commits, 160 findings → 37 unique (rule, file, line); all placeholders, secret names or planted test strings (classifier `classify.py`/`classify2.py`, values never printed) |
| 8 | Built web bundle copy | `cid=$(docker create abm-replica-web:local) && docker cp $cid:/app <scratch>/web-image && docker rm $cid` | 73 MB copied; the image was not run |
| 9 | Secret scan, web bundle | `docker run --rm -v <scratch>/web-image:/scan:ro zricethezav/gitleaks:v8.21.2 detect --no-git --source /scan --redact` | 4 findings: Next.js preview-mode keys in server-only manifests (not served, live 404) |
| 10 | Source maps | `find .next/static -name '*.map'`, grep `sourceMappingURL`, live GET `<chunk>.js.map` | 0 static maps, 0 references, 404; 65 maps under `.next/server` (not served) |
| 11 | Python dependencies | `uv export --frozen --no-dev --no-emit-project --format requirements-txt > req.txt`; `uvx pip-audit -r req.txt --no-deps --disable-pip --vulnerability-service osv --timeout 60 --format json` | 114 packages, **0 vulnerabilities** (the PyPI service timed out on the first try; OSV was used) |
| 12 | npm, production | `npm audit --omit=dev --json` in `web/` | **0** |
| 13 | npm, all | `npm audit --json` in `web/` | 5 high, dev-only (`braces` chain through `eslint-config-next`): L15 |
| 14 | XXE behaviour of lxml 6.1.3 | local `uv run --frozen python -c ...` with a `file://` entity pointing at a scratch file | `iterparse` and `fromstring` both refuse ("Entity 'e' not defined") |
| 15 | API image contents | `docker create abm-replica-api:local` + `docker export | tar -t` (listing only) + `docker rm` | no `fake_idp.py`, no `scripts/`, no `.env*`; `devstore` package present (refuses outside dev and test) |
| 16 | Live HTTP probes (no credentials, synthetic data) | curl against web :3010, API :8090, object store :8092 | see below |
| 17 | API container logs (read-only grep, counts only) | `docker logs abm-replica-api-1 | grep -c ...` | 290 of the last 300 lines are plain uvicorn access lines (L10); 0 `token=` lines in the current log |

Live probe results (replica stack):
- Web `POST /v1/research`: foreign Origin 403, no Origin 403, same Origin 401 (reached the API).
- Web `POST /api/auth/logout` with a foreign Origin: 403. Web `POST /v1/auth/login` with a
  foreign Origin: 403.
- API `POST /v1/auth/login` with a foreign Origin or none: 403. API `POST /v1/sso/discover`
  without an Origin: 403.
- API `OPTIONS /v1/research` with a foreign Origin: 405 with no CORS headers. API `GET /v1/me`
  with a foreign Origin: 401 with no CORS headers.
- Object store `OPTIONS` with a foreign Origin: 204 with no ACAO. With `http://localhost:3010`:
  ACAO echoed, no credentials. PUT to an unknown session: 404. Traversal-looking session id: 404.
- Web headers on `/login`: nosniff, `X-Frame-Options DENY`, `Referrer-Policy no-referrer`,
  Permissions-Policy, COOP, nonce CSP. No HSTS (L1).
- Web `/__design` 404; `/_next/prerender-manifest.json`, `/server.js` and `/package.json` 404;
  `/api/operator/providers` 401 without staff.
- API `/docs` and `/openapi.json` 200 without authentication (L12; internal-only in the cloud).

## Counts

- Areas checked: 18 (19 sections in the report; IDOR and RLS have their own).
- Needs fix: **0 critical, 1 high, 6 medium, 17 low**.
  - By owner: Agent 4 backend-hardening 12 (H1, M1, M2, M4, M5, L7, L8, L9, L12, L13, L14,
    L17); Agent 6 web-hardening 8 (L1 to L6, L11, L15); Agent 3 gcp-infra 3 (M3, M6, L16);
    Agent 5 ci-and-images 1 (L10).
- OK with no item: SQL injection, CSRF and same-origin checks, CORS, browser-only checks,
  webhooks and Cloud Tasks, secrets in git and the bundle.

## The two "already known" items

1. Cross-tenant sweep functions: confirmed and extended to all 12 functions owned by the
   migration user (not only the five named). Judgement: **needs fix (M1, medium)**. Use a
   NOLOGIN NOBYPASSRLS `sweeper_definer` with SELECT-only policies and column grants, as in
   0017 and 0021. Besides the privilege concern, on Cloud SQL (migration user not a superuser)
   these functions may return zero rows under FORCE RLS, which would silently stop the sweeper,
   the stall detector, the schedules tick, the Mail and drive sync and the spend checks. The
   tests run as a superuser and cannot see this.
2. Same-origin checks in route handlers: confirmed fixed. No handler compares with
   `request.url` or `nextUrl.origin`. The only `request.url` uses take `.pathname` and `.search`
   in the operator proxy.

## What I could not do, and why

- Staging-only behaviour was not tested: no cloud access, by rule. This covers Google `amr` in
  staff ID tokens, Cloud SQL role attributes, the GCS session CORS, Cloud Armor and the Redis
  buckets.
- I did not sign in on the replica (no password sign-in, by rule), so cookie attributes were
  checked in code, not on a live `Set-Cookie`.
- `gen_isolation_matrix.py --check` was not run against the replica database: the only query
  allowed there is the RLS catalog plus read-only catalog SELECTs. The same coverage check runs
  inside `test_matrix.py` (`test_every_discovered_object_is_covered_by_the_matrix`), which
  passed on my own test database.
- No full-history scan with trufflehog: gitleaks covered the history and the bundle. The
  working tree was clean, so there was nothing untracked to scan.
- No container image OS-package scan (trivy): out of this agent's list, and Agent 5's image
  checks cover it.
- pip-audit covered runtime dependencies only (`--no-dev`), which matches what the images
  install.

## Handoffs (file, line, change)

To Agent 4 backend-hardening:
- H1: `src/research_files/upload.py:180` and `src/research_files/pipeline.py:171-188`. Scan
  (fail closed) before parsing, and parse in the `documents.sandbox` child.
- M1: new `migrations/0070_*`. Add `sweeper_definer` NOLOGIN plus SELECT policies and column
  grants, move ownership of the 12 functions (list in the report), and add a non-superuser test.
- M2: `src/documents/sandbox_child.py:43-52`. Add seccomp (deny socket, connect, execve, fork),
  `RLIMIT_NPROC`, and a raw-socket test.
- M4: per-user Redis token bucket on file Ask, uploads, part retry, resume, Download everything
  and exports (`src/account_intelligence/api/routes/research_files.py:274`,
  `documents.py`, `research.py`, `downloads.py`, `exports.py`); count file Ask against the chat
  quota.
- M5: `src/account_intelligence/api/tenant_plane.py:31`. Rename the ref to
  `tenant-plane-csrf-key`, and read it in `assert_gcp_runtime` (`app/container.py:654`).
- L7: `src/identity/local_password.py:166,177`, `CapacityLimiter(4)`.
- L8: `src/identity/broker.py:396`, `policy.admin_requires_mfa or conn.require_admin_mfa`.
- L9: `src/account_intelligence/api/policy.py:116-118`, `action="approve_cap"`;
  `config/roles.yaml:38`, add `manage_settings` to step_up (owner decision).
- L12: `src/account_intelligence/api/main.py:268`, no docs or OpenAPI URLs in staging and
  production.
- L13: `src/account_intelligence/app/container.py:405`, refuse dev or test when `K_SERVICE` or
  `CLOUD_RUN_JOB` is set.
- L14: `tests/isolation/test_matrix.py:366-390`, assert 404, and add a same-tenant other-user
  route probe.
- L17: `src/identity/ports.py:41-56`, pin the resolved IP or use the egress proxy; port 443 only.

To Agent 6 web-hardening:
- L1: `web/next.config.ts:17-29`, HSTS in production.
- L2: `web/components/ws/Conversation.tsx:76`, refuse `//` and backslash in citation links.
- L3: `web/components/ws/EvidenceDrawer.tsx:59`, `safeLink`, or remove if dead (Agent 2).
- L4: `web/lib/upload.ts:15-31` plus sign-out, scope keys per user and clear on sign-out.
- L5: `web/app/v1/[[...path]]/route.ts:60-70`, content-length cap and streamed body.
- L6: `web/app/v1/[[...path]]/route.ts:21`, add `__Host-ai_preauth_once` to `AUTH_COOKIES`.
- L11: `web/app/v1/[[...path]]/route.ts:102`, log the path only.
- L15: `web/package.json`, `overrides` for `braces` when a fixed version exists, or record the
  acceptance.

To Agent 3 gcp-infra:
- M3: `infra/modules/edge/main.tf:59`, extend the auth rate-limit expression (reset, invite
  accept, SSO discover and start) and add an operator sign-in rule.
- M6: `infra/stack/main.tf:110-122`, add the secret `tenant-plane-csrf-key` (api accessor) and
  list it in the RUNBOOK.
- L16: RUNBOOK staging step to check the IAM runtime roles (members of `app_rw`): not superuser,
  not BYPASSRLS, own no table.
- Not security, found on the way: `UPLOAD_ORIGIN` is not in the web service env
  (`infra/stack/main.tf:309-314`), so the CSP `connect-src` would block browser-to-GCS uploads
  (V-FILES-1).

To Agent 5 ci-and-images:
- L10: `docker/api.Dockerfile:33` (and the worker image, if it runs uvicorn), add
  `--no-access-log`.

To the lead (VERIFY items, no owner yet):
- Staff MFA: confirm on staging whether Google Workspace ID tokens carry `amr` and `auth_time`.
  If not, staff sign-in fails closed (`src/account_intelligence/api/operator.py:113-121`) and the
  owner must choose an MFA signal: Workspace-enforced 2-step verification plus a re-auth
  `max_age`, or another IdP.
- Functional, not security: client admins cannot sign in by password while
  `admin_requires_mfa` is true and no local TOTP exists (`api/routes/auth.py:198-201`), so an
  invited admin needs an SSO connection with MFA.
