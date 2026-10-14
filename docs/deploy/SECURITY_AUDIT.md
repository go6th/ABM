# Security audit (Phase 4C, Agent 1 "security-audit")

Date: 2026-10-14. Tree: `/home/sakib/abm-replica`, branch `feat/replica`, clean working tree.
Read-only audit: no file other than this report and `docs/deploy/agents/security-audit.md` was
changed, no paid model or provider call was made, no container or cloud resource was touched.
Commands and raw counts are in `docs/deploy/agents/security-audit.md`.

Result: 18 areas checked. 1 high, 6 medium and 17 low "needs fix" items. No critical item.
The core controls hold: tenant and owner RLS (catalog clean, 688 isolation tests pass), the route
catalog, CSRF, cookies, password hashing, the document upload path, SQL building, secrets in git
and in the web bundle, and the dependency audits.

## Summary

### Needs fix, by severity

| # | Item | Severity | Owner |
| --- | --- | --- | --- |
| H1 | Research files are parsed inside the API process with no malware scan and no sandbox | high | Agent 4 backend-hardening |
| M1 | 12 cross-tenant SECURITY DEFINER functions are owned by the migration login role (they may return nothing on Cloud SQL) | medium | Agent 4 backend-hardening |
| M2 | The parser sandbox blocks network only through Python audit hooks: native code can still reach the network and the metadata server | medium | Agent 4 backend-hardening |
| M3 | The Cloud Armor auth rule misses password reset, invite accept and SSO discovery, and the API skips its per-IP bucket in the cloud | medium | Agent 3 gcp-infra |
| M4 | No per-user rate limit on file Ask (with self-approved model spend), uploads, part retry, resume, Download everything and exports | medium | Agent 4 backend-hardening |
| M5 | The CSRF key reference `tenant-plane/csrf-key` is not a valid Secret Manager id, so every cookie write in the cloud gets 503 (fails closed) | medium | Agent 4 backend-hardening |
| M6 | No CSRF signing key secret is set up in Terraform | medium | Agent 3 gcp-infra |
| L1 | No `Strict-Transport-Security` header | low | Agent 6 web-hardening |
| L2 | A Markdown "Source" link whose target starts with `//` (another host) is shown as a citation | low | Agent 6 web-hardening |
| L3 | `EvidenceDrawer` puts an unchecked URL in `href` (the component is unused) | low | Agent 6 web-hardening |
| L4 | Resumable upload session URLs in localStorage are not scoped to the user or cleared at sign-out | low | Agent 6 web-hardening |
| L5 | The `/v1` proxy reads the whole request body before the API authenticates, with no size cap | low | Agent 6 web-hardening |
| L6 | The proxy drops the `__Host-ai_preauth_once` cookie, so unticking "Keep me signed in" does nothing | low | Agent 6 web-hardening |
| L7 | No limit on how many Argon2id hashes run at once (64 MiB each, about 40 threads, on a 1 GiB API) | low | Agent 4 backend-hardening |
| L8 | A client admin can switch off admin MFA on their own IdP connection | low | Agent 4 backend-hardening |
| L9 | No step-up for `/cap/approve`, `/cap/extend` or the `manage_settings` routes (including `PUT /v1/chat/provider-key`) | low | Agent 4 backend-hardening |
| L10 | The uvicorn access log skips the allow-list logger and writes URLs with `?token=` query strings | low | Agent 5 ci-and-images |
| L11 | The web proxy's error log prints the full upstream URL, query string included | low | Agent 6 web-hardening |
| L12 | The API serves `/docs` and `/openapi.json` without authentication in every profile | low | Agent 4 backend-hardening |
| L13 | Nothing refuses `APP_ENV=dev` or `test` on Cloud Run, which would turn on the dev staff verifier and unsigned tasks | low | Agent 4 backend-hardening |
| L14 | The route probe in the isolation matrix checks for leaks but not for 404, and has no probe for another user in the same tenant | low | Agent 4 backend-hardening |
| L15 | 5 high advisories in dev-only npm packages (`braces` and `micromatch` through `eslint-config-next`) | low | Agent 6 web-hardening |
| L16 | The RLS catalog checks `app_rw` only; the cloud runtime signs in as IAM users that are members of `app_rw` | low | Agent 3 gcp-infra |
| L17 | The IdP SSRF guard resolves DNS and then connects separately (DNS rebinding window) | low | Agent 4 backend-hardening |

### Areas checked

| Area | Result | Items |
| --- | --- | --- |
| SQL injection | OK | none |
| Cross-site scripting (web, email, exports, Markdown) | OK, with two low items | L2, L3 |
| CSRF and same-origin checks | OK | none (the 98f444d follow-up is confirmed) |
| File uploads | needs fix | H1, M2, L5 |
| IDOR and object-level authorisation | OK, with one low test gap | L14 |
| Rate limiting | needs fix | M3, M4 |
| Sessions, JWT, cookies, keys | needs fix (cloud key wiring) | M5, M6, L6 |
| Secrets in git history and the web bundle | OK | none |
| Password hashing | OK, with one low item | L7 |
| MFA (client admins, 6th staff) | OK, with low items | L8, L9 |
| CORS (API, upload bucket, dev store) | OK | none |
| Tokens in browser storage | OK, with one low item | L4 |
| Checks done only in the browser | OK | none |
| RLS and definer functions | needs fix (definer owner) | M1, L16 |
| Webhooks and Cloud Tasks | OK | none |
| SSRF | OK, with one low item | L17 |
| Source maps, debug pages, default credentials | OK, with low items | L1, L12, L13 |
| Sensitive data in logs | OK, with low items | L10, L11 |
| Vulnerable dependencies | OK, with one low item (dev only) | L15 |

## Details by area

### 1. SQL injection: OK

Checked: every `text()`, `execute()` and `exec_driver_sql()` call in `src/` whose first argument
is not a constant string (AST scan, see the agent log), plus every dynamic `EXECUTE` in the
migrations.

Evidence:
- 53 call sites. 34 are f-strings or concatenations with a `nosec B608` or `noqa: S608` reason.
  Each one puts in identifiers taken only from fixed allow-lists and binds every value. Examples:
  `documents/repo.py:120-133` (`allowed` set), `replica/settings.py:205` (`CLIENT_FIELDS`),
  `tenancy/lifecycle.py:241` (validated deletion plan), `obs/canary.py:49` (`PROBE_TABLES`),
  `account_intelligence/db/owners.py:35` (`OWNED_ITEMS`).
- The 19 sites without an annotation pass a variable that holds a constant string (for example
  `pilot/usefulness.py:36`, `identity/membership.py:67`), or SQL built by an allow-listed builder.
  The user-defined table query (`api/routes/accounts.py:331`) is compiled by
  `views/query.py:193-264`. Column keys must match `^[a-z_]{1,40}$` and the table spec, operators
  must be in the column's allowed set, direction is `Literal["asc","desc"]`, and every value, the
  cursor and the search text are bound. Sheet queries (`replica/sheets.py:381-460`) choose filters
  and sorts only from fixed `Sheet` objects.
- Migrations: dynamic SQL uses `format('%I')` only (`0002_rls.sql:50-84`, `0063` `fill_owner`).
  `erase_rows` (`0020_tenancy.sql:152-190`) checks the table name against a fixed list first.

### 2. Cross-site scripting: OK (L2, L3 low)

Checked: raw HTML sinks in `web/`, Markdown rendering, links built from data, HTML in emails and
exports, CSV and Excel formula injection.

Evidence:
- No `dangerouslySetInnerHTML`, `innerHTML`, `document.write` or `eval` in `web/app`,
  `components`, `lib` or `ui` (grep returned 0 hits).
- Markdown: `components/ws/Conversation.tsx:91` uses `react-markdown` with `skipHtml`, removes
  images, and passes external links through `safeLink`, which allows absolute http(s) only and
  refuses `//`, credentials and control characters (`lib/render.ts:5-17`). React 19.3 also blocks
  `javascript:` URLs.
- Links from data (`AnswerView.tsx:38`, `accounts/[domain]/page.tsx:95`, `SheetView.tsx:23`,
  `DataGrid.tsx:19,57`) all go through `safeLink`.
- CSP from `web/proxy.ts`, observed live on `/login`: per-request nonce, `strict-dynamic`,
  `object-src 'none'`, `frame-ancestors 'none'`, and no `unsafe-inline`.
- Emails: only `UnconfiguredMailer` exists (`tenancy/ports.py:97`). Templates take identifiers as
  parameters, so no HTML mail renderer exists that could carry injected markup.
- Exports: OOXML text is escaped (`exports/ooxml.py:44-58`). CSV and XLSX cells pass
  `csv_cell`/`safe_cell`, which prefix `= + - @ \t \r` with an apostrophe (`views/render_safe.py`,
  `replica/table_export.py:84-86`, `audit/export.py:46-47`, `views/packages.py:123`).
  `Content-Disposition` filenames are generated by the server (`_stem` keeps letters, digits and
  hyphens only).

**L2 (low, Agent 6).** `web/components/ws/Conversation.tsx:76`: a model-written link
`[x](//evil.example "Source 1")` passes `link.startsWith("/")` and becomes a same-tab citation to
another host (phishing through prompt-injected content, not script). Change: require
`link.startsWith("/") && !link.startsWith("//") && !link.includes("\\")`.

**L3 (low, Agent 6).** `web/components/ws/EvidenceDrawer.tsx:58-59`: `href={current.url}` is not
passed through `safeLink`. Nothing imports the component. Change: wrap the URL in `safeLink`, or
remove the component if Agent 2 lists it as dead.

### 3. CSRF and same-origin checks: OK

Checked: the CSRF token and the same-origin check on every state-changing route, including model
preferences, limit requests, part retry, resume research, downloads and SSO discovery, and every
Next.js route handler's origin comparison.

Evidence:
- API: `api/guard.py:90-99` requires the session-bound HMAC token (`identity/csrf.py:18-29`,
  constant-time compare) on every non-GET/HEAD/OPTIONS call that uses a cookie. This is enforced
  as a router dependency for all of `/v1` (`api/routes/__init__.py:50`), so it covers
  `PUT /v1/me/model-preferences`, `POST /v1/limits/requests`,
  `POST /v1/documents/{id}/parts/{part}/retry`, `POST /v1/research/resume`,
  `POST /v1/files/{id}/resume` and `POST /v1/accounts/{domain}/download-everything`. The guarantee
  test is `test_csrf_is_required_on_every_state_changing_cookie_call`.
- Anonymous auth routes (login, resend, verify, reset request, reset, invite accept, enroll, SSO
  discover) call `_require_same_origin` (`api/routes/auth.py:304-324`), which compares `Origin`
  with `PUBLIC_BASE_URL`. SAML ACS is cross-site by protocol and is protected by the pre-auth
  cookie and the assertion checks.
- Web: the `/v1` proxy (`web/app/v1/[[...path]]/route.ts:44-51`), sign-out, the operator session
  and the operator proxy all use `isSameOriginRouteRequest` from `web/lib/same-origin.ts`.
  `grep -rnE "request\.url|nextUrl\.origin"` finds only `new URL(request.url).pathname` and
  `.search` in the operator proxy, which extract the path and are not an origin comparison. No
  handler compares with the server's own address (the 98f444d follow-up is confirmed).
- Live, replica web: `POST /v1/research` with a foreign Origin returned 403, with no Origin 403,
  and with the same Origin 401 (it reached the API). `POST /api/auth/logout` with a foreign Origin
  returned 403. API direct `POST /v1/auth/login` and `POST /v1/sso/discover` without a matching
  Origin returned 403.

### 4. File uploads: needs fix (H1 high, M2 medium, L5 low)

Checked: type detection from content, malware scan before parsing, the parser sandbox, size
enforcement on the server, path traversal in keys and names, and the dev object store's session
checks.

The document path (input mode "file") is OK:
- The type comes from content (`research_files/detect.py:80-130`, magic numbers and zip
  inspection) and is compared with the declared type, then the malware scan runs, all before any
  parse (`documents/service.py:387-412`).
- The scanner fails closed (`documents/scan.py`): an unavailable scanner means
  `scan_unavailable`, and anything but clamd in staging or production rejects every upload.
- Size is checked when the upload opens, against storage metadata at completion
  (`documents/service.py:176-185, 247-262`), and through the GCS session's declared length.
- Keys contain no names (`tenants/<t>/...`). `LocalBlobStore.path` and the dev store's `target`
  refuse escaping keys. Display names drop path parts and control characters (`clean_name`).
  Archive members are checked for traversal, links and bombs (`research_files/parsers/archives.py`).
- XXE: lxml 6.1.3 refuses external entities with the default `iterparse` and `fromstring` (tested
  locally against a file entity).
- Dev object store (`src/devstore/server.py`): 256-bit session ids matched by `SID`, session
  expiry, declared total enforced, chunks must not skip ahead, CORS only for
  `DEVSTORE_ALLOW_ORIGIN`, and it refuses to run outside dev and test. Live: PUT to an unknown
  session returned 404, a traversal-looking id returned 404, and a foreign Origin got no
  `Access-Control-Allow-Origin`.

**H1 (high, Agent 4).** Research files (`/v1/files/uploads` and following) skip both the scan and
the sandbox:
- `/tasks/file-process` runs on the API service (`api/routes/tasks.py:138-149`) and calls
  `research_files.pipeline`, which parses in-process: `pipeline.py:179`
  `parser.units(handle, ...)`.
- The parsers are Pillow, pyarrow (parquet), openpyxl, lxml, pypdf, zip, tar and gzip
  (`research_files/parsers/*`), on files up to 10 GiB (`config/research_files.yaml:7`).
- There is no scanner call anywhere in `src/research_files/` (grep for `scan` found only
  `pii_scan`).
- A parser memory-corruption or decompression bug would therefore run inside the API, which has
  the database connection, Secret Manager access and the metadata server.

Change: after `detect()` in `research_files/upload.py:180` (or at the start of
`pipeline._run`), run the same `ScanPort` the documents use (fail closed: unavailable means
rejected). Run `parser.units` in the `documents.sandbox` child with the same limits: add a
research-file mode to `sandbox_child.py` that streams units as JSON lines. Until that exists,
store archive, media and parquet files without parsing them.

**M2 (medium, Agent 4).** `documents/sandbox_child.py:31-52` blocks network, processes and writes
with `sys.addaudithook`, and sets `RLIMIT_AS`, `CPU`, `FSIZE` and `CORE`. Audit hooks only see
Python-level calls. Native parser code (lxml/libxml2, Pillow and others) can still open sockets,
including to `169.254.169.254` (service-account token) and to anything on the VPC. `RLIMIT_NPROC`
is not set. Change: in `_limits`, add a seccomp filter after `prctl(PR_SET_NO_NEW_PRIVS)`, for
example with `pyseccomp`/libseccomp, that returns EPERM for `socket`, `connect`, `execve` and
`fork`/`clone` without `CLONE_THREAD`. Also set `RLIMIT_NPROC` to 0 or 1. Keep the audit hook as
a second layer. Prove it with a test that tries to open a raw socket.

**L5 (low, Agent 6).** `web/app/v1/[[...path]]/route.ts:60-70` buffers `request.arrayBuffer()`
before the API has authenticated the caller, with no size limit. The same-origin check is only a
browser control, so a script can send bodies up to Cloud Run's 32 MiB per request into a 512 MiB
instance. Change: refuse with 413 when `content-length` is over the route's maximum (about 9 MiB
for `PUT /v1/files/uploads/{id}/parts/{n}`, the 8 MiB part plus headroom, and 1 MiB elsewhere),
and stream the body (`body: request.body`, `duplex: "half"`) instead of buffering it.

### 5. IDOR and object-level authorisation: OK (L14 low)

Checked: every route that takes an id refuses another tenant's and another user's object, and
whether the isolation matrix and `test_ownership.py` cover every route and table.

Evidence:
- Run on my own test database: `tests/isolation/test_matrix.py` and
  `tests/isolation/test_ownership.py` gave 688 passed in 132 s. The matrix covers 174 tables,
  1 view, 38 definer functions, 216 routes, 10 storage layouts, 1 cache, 13 events and 12 jobs,
  with a coverage check that fails on any uncovered or stale object.
- `config/ownership.yaml` classifies all 171 tenant tables: 108 owned, 62 tenant-level, plus
  `ownership_backfill`.
- Every tenant route runs as the verified user in user scope (`api/guard.py:110-114`). Worker
  tasks act as the item's owner (`db/owners.py`, `api/routes/tasks.py:62-69`). The only explicit
  `system=True` transaction is the owner lookup itself.

**L14 (low, Agent 4).** The route probe (`tests/isolation/test_matrix.py:366-390`) asserts that no
canary value leaks and that the status is below 500. It does not assert 404, and it probes only
as another tenant. No route probe covers another user of the same tenant: owner isolation is
proved at the table level only. Change: (a) for `tenant` probes whose path names an object of A,
assert `reply.status_code == 404` unless the matrix entry gives an explicit `status`. (b) Add a
second parametrised run with principal `matrix-a2`, a member of tenant A that is not the owner
of A's canaries, and assert 404 with nothing leaked.

### 6. Rate limiting: needs fix (M3, M4 medium)

Checked: sign-in, reset, invite, SSO discovery, Ask, uploads, part retry, resume, downloads and
exports.

Evidence:
- App buckets exist only for auth: `identity/rate_limit.py` is called for login, enroll, resend,
  verify, reset request, reset, invite accept and SSO discover (`api/routes/auth.py:140-554`,
  `sso.py:98`).
- With Redis configured (the cloud), the per-IP bucket is skipped (`identity/rate_limit.py:97`),
  because the API's TCP peer is the web service. The per-IP limit is left to Cloud Armor.
- Cloud Armor (`infra/modules/edge/main.tf:53-72`): the auth rule matches only
  `^/v1/auth/(login|email/(resend|verify)|password/enroll)$`. The general rule is 600 requests
  per minute per IP (`infra/stack/main.tf:403`).
- Ask uses the per-user chat quota (`api/routes/ask.py`), and research uses the research quota
  (`api/routes/research.py:136`).

**M3 (medium, Agent 3).** `infra/modules/edge/main.tf:59`: extend the auth expression to
`^/v1/(auth/(login|email/(resend|verify)|password/(enroll|reset(/request)?)|invite/accept)|sso/(discover|[a-z0-9-]+/start))$`,
and add a second, looser rule for `^/api/operator/(login|callback|session)$`. Today reset
requests, invite-token guessing and SSO discovery (domain enumeration) are limited per IP only by
the 600 per minute default. The per-account and per-token buckets still apply.

**M4 (medium, Agent 4).** `POST /v1/files/{id}/ask` (`api/routes/research_files.py:274`) makes a
paid model call per request. `approve=true` from the caller skips the cost gate
(`research_files/modelsvc.py:181`), and the call counts against no quota. Uploads
(`/v1/files/uploads`, `/v1/documents/uploads`), part retry, `/v1/research/resume`,
`/v1/files/{id}/resume`, `/v1/accounts/{domain}/download-everything` (a new `request_id` gives a
new archive build) and `/v1/exports` have no per-user limit either. Change: add a per-user
token bucket on the shared Redis store (the same `RedisRateStore` as auth), keyed by tenant and
user, as a dependency on these routes, with values in a reviewed config file (for example
`config/rate_limits.yaml`). Also count file Ask against the user's chat quota (`usage_periods`).
These are money and compute limits, not a correctness fix.

### 7. Sessions, JWT, cookies, keys: needs fix in the cloud wiring (M5, M6 medium; L6 low)

Evidence, OK parts:
- Session cookie `__Host-ai_session`, set `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`
  (`api/routes/auth.py:118-129`, `sso.py:74-90`). The `__Host-` prefix is enforced when the
  policy loads (`identity/sessions.py:73`).
- Idle 30 minutes and absolute 8 hours (`config/session.yaml`). Only the token's SHA-256 is
  stored. Sessions rotate at sign-in and are revoked on password reset (`auth.py:530-544`).
- Operator cookies are `__Host-6th_operator_*` over https, `SameSite=Strict` for the session
  (`web/lib/operator-oidc.ts:74-83`). The stored Google ID token's own `exp` bounds the session.
- JWTs are pinned to RS256 with audience, issuer and required claims, for tenants
  (`api/auth.py:90-97`) and staff (`api/operator.py:84-92`). The bearer role is re-checked
  against membership (`api/auth.py:140-150`).
- The CSRF key comes from the secret store and must be at least 32 bytes
  (`api/tenant_plane.py:129-134`). Cloud reads it from Secret Manager (`cloud_plane`).

**M5 (medium, Agent 4).** `api/tenant_plane.py:31`: `CSRF_KEY_REF = "tenant-plane/csrf-key"`. A
Secret Manager secret id may contain only letters, digits, `-` and `_`. `SecretManagerSource.get`
(`platform/gcp/adapters.py:177-186`) therefore raises `NotConfigured`, and every
cookie-authenticated unsafe call, and `/v1/sso/session`, answers 503 in staging and production.
This fails closed, so it is a deploy blocker rather than a hole. Change: rename the reference to
`tenant-plane-csrf-key`. In `assert_gcp_runtime` (`app/container.py:654`), read it once at start
so a missing key stops the deploy instead of failing every request.

**M6 (medium, Agent 3).** `infra/stack/main.tf:110-122`: add
`"tenant-plane-csrf-key" = ["api"]` to `local.secrets`. The value is 32 or more random bytes,
added out of band. Add it to the RUNBOOK secret list. Coordinate the exact name with Agent 4
(M5).

**L6 (low, Agent 6).** `web/app/v1/[[...path]]/route.ts:21`: `AUTH_COOKIES` lacks
`__Host-ai_preauth_once`, the "Keep me signed in = off" marker (`api/routes/sso.py:69-90`), so
SSO sessions always get the persistent 8-hour cookie. Change: add the name to `AUTH_COOKIES`.

### 8. Exposed secrets: OK

Checked: gitleaks 8.21.2 with `--redact` over the main repository's full history (all branches,
438 commits) and over the built web image (`abm-replica-web:local`, copied out with
`docker create` and `docker cp`).

Evidence:
- History: 160 findings, which are 37 unique (rule, file, line). All are placeholders or planted
  test strings (classified without printing values: entropy, word content, context):
  - `docs/providers/sources/p_people/api-reference.md` (lines 69 to 841),
    `docs/providers/sources/p_org/people.md:146,158`,
    `docs/providers/sources/p_scrape/scrape-feature.md:1398`: vendor documentation examples
    (`curl-auth-header`, `generic-api-key`), first in commit `b01f1ad9a3` (also `fb60f1e5fa`
    under `ABM-Generator-broker/`).
  - `infra/stack/main.tf:367` (`hashicorp-tf-password`, first `f4e36559b2`): a Secret Manager
    secret name, not a value.
  - `tests/connectors/test_contracts.py:348` (first `aae200993c`), `tests/runs/test_stop.py:17`
    and `tests/research_files/fixtures.py:130` (first `3206a9ef46`): strings planted on purpose
    to test the key scanner and redaction.
- Web image: 4 findings, all Next.js preview-mode signing and encryption keys made per build in
  `.next/prerender-manifest.json:69-70` and `.next/server/preview-props.json:3-4`. These are
  server-only files and not served: live `GET` for `/_next/prerender-manifest.json`,
  `/.next/prerender-manifest.json`, `/server.js` and `/package.json` all returned 404. No
  provider key name or `sk-ant-` string is in `.next/static`. The image environment holds no
  secret variables (names only: PATH, NODE_VERSION, YARN_VERSION, NODE_ENV,
  NEXT_TELEMETRY_DISABLED, PORT, HOSTNAME). No `.env*` file is in the API or web image.

### 9. Password hashing: OK (L7 low)

Argon2id with `time_cost` 3, `memory_cost` 64 MiB, parallelism 2, a 16-byte salt and a 32-byte
hash (`config/auth.yaml:13-18`). The loader refuses anything below 64 MiB or `t=2`
(`identity/local_password.py:87-88`). Verification always does Argon2 work, including for unknown
accounts. Passwords must be 12 to 128 characters, with a common-password list and checks against
context words. Lockout is `local_auth_failure`.

**L7 (low, Agent 4).** `identity/local_password.py:166,177` run hashing on anyio's default
thread limiter (40 threads). 40 × 64 MiB = 2.5 GiB on a 1 GiB API instance, so a distributed
burst of sign-ins can kill the instance with out-of-memory. Change: a module-level
`anyio.CapacityLimiter(4)` passed to `to_thread.run_sync(..., limiter=...)`.

### 10. MFA: OK (L8, L9 low)

Evidence:
- Client admins: password sign-in is refused for any admin while `admin_requires_mfa` is true
  (`api/routes/auth.py:198-201`). SSO sign-in as admin requires `mfa` in `amr`
  (`identity/broker.py:396-398`).
- Step-up (an MFA sign-in within 10 minutes) is enforced on the server for
  `manage_users, manage_sso, manage_connectors, export_audit, grant_support, request_deletion,
  approve_cap` (`config/roles.yaml:38`, `api/guard.py:100-107`), and for pause approval in
  `spend/pause.py:292`.
- 6th staff: every operator route needs a staff token whose `amr` has `mfa` and whose
  `auth_time` falls within the step-up window (`api/operator.py:113-121, 205-208, 233-235`). The
  web callback refuses a session without `mfa_fresh`.

**L8 (low, Agent 4).** `identity/broker.py:396`:
`admin_mfa = conn.require_admin_mfa and self.policy.admin_requires_mfa` lets a client admin turn
admin MFA off for their tenant (it needs one step-up first). Change: when the platform policy
requires MFA, ignore the per-connection flag:
`admin_mfa = self.policy.admin_requires_mfa or conn.require_admin_mfa`.

**L9 (low, Agent 4).** `api/policy.py:116-118`: `/cap/approve`, `/cap/reject` and `/cap/extend`
are admin-only with `action=None`, so they have no step-up. These routes are on the Phase 6
removal list. Until then, register them with `action="approve_cap"`. `manage_settings`
(`PUT /v1/settings`, branding, `PUT /v1/chat/provider-key`, `PUT /v1/documents/policy`) is not in
`step_up`. Change: add `manage_settings` to `config/roles.yaml:38` step_up, if the owner agrees,
since it changes data policy and model keys.

Not verified: Google Workspace ID tokens normally do not carry `amr`. If staging confirms that,
no staff member can pass the operator MFA check (fail closed). See the agent log, handoffs.

### 11. CORS: OK

- The API has no CORS middleware (grep `CORSMiddleware` found 0). Live: a preflight with a
  foreign Origin returned 405 with no `Access-Control-*` headers. A GET with a foreign Origin
  returned 401 with no ACAO.
- The upload bucket has no bucket-wide CORS (`infra/modules/gcs`). GCS resumable sessions are
  opened with the web origin (`research_files/blobstore.py:297-311`), so only that origin gets
  CORS on that one session URI. No wildcard is used anywhere, and credentials are never sent to
  storage.
- Dev store: ACAO only for the configured origins, no credentials (see 4).

### 12. Tokens in browser storage: OK (L4 low)

- The CSRF token is kept in a module variable (`web/lib/api.ts:27-37`). Session cookies are
  HttpOnly. `localStorage` holds only `ai.schedules` (cadence choices) and `upload:v1:*` entries.
- An `upload:v1:*` entry (`web/lib/upload.ts:15-31`) holds the document id, the file name and
  size, and the resumable session URL. That URL lets the holder PUT bytes into that one
  document's quarantine object, up to the declared size, until it expires. It grants no read. The
  object is still type-checked and scanned before anything opens it. The API's `expires_at` is
  `upload_ttl_minutes`. A GCS session URI itself lives up to a week, and the dev store's 48 hours
  (`DEVSTORE_SESSION_TTL_S`).

**L4 (low, Agent 6).** Entries are keyed by file name, size and modified time, not by user, and
sign-out (`web/app/api/auth/logout/route.ts`, plus the client sign-out path) does not remove
them. The next person on a shared browser sees the earlier user's paused file names
(`pausedUploads()` in `DocumentDrop`). Change: prefix keys with the signed-in user's id, and
remove every `upload:v1:` key on sign-out.

### 13. Checks done only in the browser: OK

Every `/v1` and `/operator/v1` route has an explicit catalog entry with server-side roles,
principal types and audit class. The guard refuses unlisted routes (`api/guard.py:64-69`).
`scripts/check_route_catalog.py` prints "every route has a policy, an audit class and a session
tenant" (218 operations: 100 GET, 98 POST, 14 PUT, 5 DELETE, 1 PATCH). Web gates
(`me.role === "admin"`, `capabilities`) only hide buttons, and the matching API routes refuse the
same roles. For example `POST /v1/research` is limited to admin, analyst and seller, settings and
users to admin, and staff routes check `operator_roles` in `require_staff`/`require_operator`.

### 14. RLS: OK, with the definer owner to fix (M1 medium, L16 low)

Evidence:
- `check_rls_catalog.sql` on the replica database (migration head 0068): exit 0, **0 rows**
  (forced RLS everywhere, `app_rw` NOSUPERUSER NOBYPASSRLS, owns nothing, no partition grants,
  no unscoped policy, NOT NULL `tenant_id`, app_rw policies with USING and WITH CHECK, no DELETE
  on append-only tables).
- Owner rule (0063): all 108 tables with `owner_user_id` have forced RLS, NOT NULL
  `owner_user_id`, and an `app_rw` policy with `owner_user_id` in both USING and WITH CHECK. The
  6 other policies on owned tables are for `operator_resolver`, `public_reader` and
  `pilot_reader` only. `test_ownership.py` passes.
- Roles: `app_rw` LOGIN NOINHERIT; definer roles `governor_definer`, `identity_*`,
  `operator_resolver`, `pilot_reader`, `tenant_eraser` and `tenant_provisioner` are NOLOGIN and
  NOBYPASSRLS.

**M1 (medium, Agent 4, new migration 0070).** 12 SECURITY DEFINER functions are owned by the
migration user (`postgres` here) rather than a NOLOGIN role:
- `runs_orphaned` (0050:15), `runs_stalled` (0067:44), `research_files_orphaned` (0051:218),
  `documents_orphaned` (0052:311), `document_parts_orphaned` (0066:71),
  `due_scheduled_refreshes` and `tenants_with_waiting_jobs` (0055:7,24),
  `library_publication_counts` (0058:7), `maildrive_connections_due` (0059:7),
  `spend_runs_to_check` (0015:234), `tenants_with_credit_activity` (0057:31) and
  `write_platform_tombstone` (0043:60).
- Each pins `search_path`, returns identifiers only, revokes PUBLIC and grants EXECUTE to
  `app_rw` (catalogued in `tenancy/catalog.py:110` JOBS).

The pattern needs a fix for two reasons:
1. The replica migration user is a superuser, which is why the functions see every tenant. On
   Cloud SQL the migration user is not a superuser. If it lacks BYPASSRLS, then under FORCE RLS,
   with policies only `TO app_rw`, these functions return **zero rows**. The orphan and stall
   sweepers, the schedules tick, the Mail and drive sync and the spend checks would silently do
   nothing (tests run as a superuser, so they cannot see this).
2. If it does have BYPASSRLS, the runtime's cross-tenant reads rest on a LOGIN role whose
   credential sits in Secret Manager (`migrator-database-url`).

Change, as in 0017 and 0021: create `sweeper_definer NOLOGIN NOBYPASSRLS NOINHERIT`. For each
table read, add `CREATE POLICY sweeper_read ON <t> FOR SELECT TO sweeper_definer USING (true)`
and column-level `GRANT SELECT (<only the columns used>)`. The tables are `account_runs`,
`run_leases`, `research_files`, `documents`, `document_parts`, `accounts`, `research_jobs`,
`library_publications`, `connector_connections` (or the Mail and drive table that 0059 reads),
`wallet_ledger` and `credit_entries` as used. Then `ALTER FUNCTION ... OWNER TO sweeper_definer`
for the 11 readers. Give `write_platform_tombstone` its own NOLOGIN owner with INSERT on
`platform_tombstones` only. Then add a test that runs each function on a database where the
migration role is not a superuser (or `SET ROLE` to a NOSUPERUSER owner) and expects rows.

**L16 (low, Agent 3).** The catalog checks `app_rw` only. In the cloud the runtime logs in as IAM
database users that are granted `app_rw` (`docs/runbooks/database.md:11-12`). Change: add a
RUNBOOK staging step that runs
`SELECT rolname, rolsuper, rolbypassrls FROM pg_roles WHERE pg_has_role(rolname, 'app_rw', 'MEMBER') AND rolname <> 'app_rw'`
and checks that those roles own no table. Expect no superuser, no BYPASSRLS and no owner.

### 15. Webhooks and Cloud Tasks: OK

- There is no inbound webhook route: no `/webhook` path among the 218 operations, and the alert
  channels raise `NotConfigured`.
- Every `/tasks/*` route calls `_authorise` first (`api/routes/tasks.py:73-88`, called at lines
  96, 144, 169, 187, 218, 231, 256, 271, 334 and 355). `verify_task_token`
  (`platform/gcp/adapters.py:189-202`) checks Google's signature, the issuer and the audience
  (`verify_oauth2_token`), and requires `email == TASK_SERVICE_ACCOUNT` with `email_verified`.
- `ALLOW_UNAUTHENTICATED_TASKS` cannot take effect in the cloud for three reasons:
  `forbidden_in=CLOUD` in `config.py:296-298`; it applies only when
  `container.env in ("dev","test")` (`tasks.py:76-81`); and `assert_gcp_runtime` refuses to start
  without a task verifier and audience (`app/container.py:667-668`).
- The API ingress is `INGRESS_TRAFFIC_INTERNAL_ONLY` (`infra/stack/main.tf:284`). Scheduler jobs
  use an OAuth token to the Cloud Run Jobs API (`infra/modules/scheduler/main.tf:35-40`).

### 16. SSRF: OK (L17 low)

- The platform does not fetch pages itself. Provider clients use a fixed https `base_url` with
  `follow_redirects=False` (`connectors/http.py:51-69`). URLs handed to providers are checked
  before the call and again on the final URL a provider reports after redirects
  (`connectors/security.py`): https only, port 443, no userinfo, `localhost`/`*.internal` and
  metadata names refused, every resolved address public, `169.254.169.254`, `fd00:ec2::254` and
  IPv4-mapped addresses refused.
- Mail and drive uses fixed Google API hosts. The IdP calls (tenant-admin URLs) are https only,
  resolve to public addresses only, and do not follow redirects (`identity/ports.py:41-56,
  120-125`; guarantee `test_idp_calls_refuse_private_addresses_and_redirects`). Return paths are
  local only (`identity/broker.py:432-442`, `web/lib/operator-oidc.ts:86-95`).

**L17 (low, Agent 4).** `identity/ports.py:41-56` resolves the host, then httpx resolves it again
when it connects (a DNS rebinding window), and it allows any port. Because only https is allowed,
the HTTP-only metadata server cannot be reached, so the impact is small. Change: connect to the
validated address (a custom httpx transport that pins the resolved IP and keeps SNI and Host), or
route these calls through `CONNECTOR_EGRESS_PROXY`. Allow port 443 only.

### 17. Source maps, debug pages, default credentials, demo users, fake-idp: OK (L1, L12, L13 low)

- Source maps: 0 `.map` files under `.next/static` in the built image, and no `sourceMappingURL`
  in static chunks. The 65 maps are under `.next/server`, which is not served. Live:
  `<chunk>.js.map` returned 404. `poweredByHeader: false`. `/__design` returned 404 in the
  production build.
- Defaults and demo users: `fake_idp.py` lives in `ops/dev/` and is not in the API image (image
  listing). The seed script `scripts/seed_replica_demo.py` needs `APP_ENV` dev, local or test
  plus `DEMO_PASSWORD`, and `scripts/` is not in the image. `app_rw`'s password is set only from
  `APP_RW_PASSWORD` (`migrations/versions/0002_rls.py:23-31`). The dev staff verifier works only
  with `APP_ENV` dev or test (`api/operator.py:153-160`). The dev object store refuses other
  profiles. Terraform deploys no fake-idp or devstore.

**L1 (low, Agent 6).** No `Strict-Transport-Security` header (live `/login` headers;
`web/next.config.ts:17-29`). Change: add
`{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }` when
`NODE_ENV === "production"`.

**L12 (low, Agent 4).** `api/main.py:268`: `FastAPI(...)` keeps `/docs`, `/redoc` and
`/openapi.json` in every profile (live 200 on the replica). The API is internal-only in the cloud,
so this is defence in depth. Change: pass `docs_url=None, redoc_url=None, openapi_url=None` when
`APP_ENV` is staging or production. `scripts/export_openapi.py` calls `create_app().openapi()`
directly, so the snapshot is unaffected.

**L13 (low, Agent 4).** Nothing ties the dev profiles to a local machine. A Cloud Run deploy
with `APP_ENV=dev` would accept any bearer string as an MFA'd operator (`DevStaffVerifier`) and
honour `ALLOW_UNAUTHENTICATED_TASKS`. Change: in `build_container` (`app/container.py:405`), raise
`RuntimeMisconfigured` when `APP_ENV` is dev or test and `K_SERVICE` or `CLOUD_RUN_JOB` is set.

### 18. Sensitive data in logs: OK (L10, L11 low)

- Application logs go through the JSON formatter, which keeps only catalogued fields and reduces
  exceptions to class and hash (`obs/logging.py`, `obs/scrub.py`; guarantee
  `test_log_lines_carry_only_allowed_fields`). It is installed in the API and the worker
  (`api/main.py:67`, `worker/__main__.py:79`).
- The sandbox's stderr carries an error class only. Reset and verification secrets travel in URL
  fragments, never query strings (`api/routes/auth.py:249-264`).

**L10 (low, Agent 5).** `docker/api.Dockerfile:33` (and the worker image's uvicorn command, if
any) runs uvicorn with its default access log. That logger has its own handler, so its lines skip
the allow-list. In the replica container 290 of the last 300 lines were plain access lines. They
include the full path and query, for example `GET /v1/downloads/{job}/parts/{n}?token=...`
(`downloads.py:117`), `PUT /v1/documents/{id}/content?token=...` (`documents.py:156`), and
account domains in paths. Change: add `"--no-access-log"` to the uvicorn command. Cloud Run's
own request log remains.

**L11 (low, Agent 6).** `web/app/v1/[[...path]]/route.ts:102` logs
`targetUrl.toString()`, which includes the query, on upstream failure. Change: log
`request.nextUrl.pathname` only.

### 19. Vulnerable dependencies: OK (L15 low)

- Python: `uv export --frozen --no-dev --no-emit-project` gave 114 pinned packages.
  `pip-audit --vulnerability-service osv` found **0 known vulnerabilities**. The PyPI service timed
  out, so OSV was used.
- npm, production (`npm audit --omit=dev`): **0** vulnerabilities.

**L15 (low, Agent 6).** The full `npm audit` (including dev) finds 5 high advisories, all through
`eslint-config-next` → `@next/eslint-plugin-next` → `fast-glob` → `micromatch` → `braces`
(GHSA-vfj7-8cjw-p6xm, a stack-exhaustion DoS). This is lint-time only and not in the image.
Change: add `"overrides": { "braces": "<first fixed version>" }` in `web/package.json` once a
fixed release exists, or accept and record it. Do not run `npm audit fix` (it proposes
downgrading to 14.2.35).

## Not verified here (needs staging)

- Whether Google Workspace ID tokens carry `amr`/`auth_time` for the staff MFA check
  (`api/operator.py:115`). If they do not, staff sign-in fails closed.
- Whether Cloud SQL's migration user has BYPASSRLS (it decides which side of M1 applies).
- GCS resumable-session CORS from the web origin (V-FILES-1). Separately, `UPLOAD_ORIGIN` is not
  set in the Terraform web env, so the CSP `connect-src` would block direct uploads. This is
  functional, not security, and is passed to Agent 3.
- Cloud Armor rule behaviour and Redis-backed buckets at the real edge.
