# Threat model: the Phase 3 tenant plane (update)

Scope: `/v1` (tenant), `/v1/sso` (sign-in), `/operator/v1` (staff), the web app, connectors,
exports. Method: STRIDE per asset; each threat names the control and the test that proves it.
Highest impact: cross-tenant exposure.

| # | Threat | Asset | Control | Proof |
|---|---|---|---|---|
| T1 | Tenant B reads, lists, searches or downloads tenant A's rows, files or events | all tenant data | forced RLS on every tenant table, policies `TO app_rw`, `tenant_txn` from the verified identity only; storage paths `tenants/{id}/...` with signed, tenant-bound download tokens; tenant-prefixed cache keys | generated isolation matrix (every table, view, function, route, storage layout, cache purpose, event, job), with the authorisation layer replaced (TN3); a broken policy is caught |
| T2 | A request names another tenant (body, path, query, header) | tenant boundary | no route accepts a tenant field (route catalog check); `{domain}` and ids resolve inside the session tenant only; operator tenant targets only on lifecycle and quota routes | `scripts/check_route_catalog.py`; matrix probes |
| T3 | A new route ships without authorisation | every route | deny by default: a route missing from the catalog is 403; every route has roles, an audit class and a tenant source | catalog check, `test_ws0_foundations.py` |
| T4 | Forged or replayed sign-in (token substitution, XML signature wrapping, assertion replay, state or PKCE tampering, open redirect, login CSRF) | identity | PyJWT and signxml (no hand-rolled crypto or XML), algorithm allow-list, issuer and audience, nonce, PKCE S256, state bound to a pre-auth cookie, single-use, assertion id cache, same-origin `return_to` | WS2 negative suite |
| T5 | Session theft or fixation; CSRF | sessions | `__Host-` cookie, HttpOnly, Secure, SameSite; token hash at rest; rotation at sign-in; idle and absolute timeouts; HMAC CSRF token on unsafe cookie requests | WS2 session tests |
| T6 | A removed or demoted member keeps access | memberships | membership re-check with a 60 s cache; suspension and removal end access within it | `test_ws2_membership.py` |
| T7 | Privilege escalation inside a tenant | roles | one action table; last admin protected; no self-promotion; step-up for administrative actions | `test_ws2_roles.py` |
| T8 | Staff reads tenant data without consent | tenant data | no standing staff access; support only through a tenant-admin grant resolved per request; read-only unless a second write grant; every call visible to the tenant; no break-glass | `test_ws8_admin.py`, staff probes |
| T9 | Repudiation: an action without a trace | audit | catalog audit class per route; reads recorded (fail closed); admin and export actions fail closed; per-tenant hash chain detects edits and deletions | `test_ws3_audit.py` |
| T10 | Stored XSS, URL-scheme or spreadsheet formula injection through tenant or provider text | web app, exports | every cell a React text node; links allow-listed to http(s) with safe `rel`; CSV and Excel cells neutralised; nonce CSP from `proxy.ts` | `test_ws4_render_safe.py`, web unit tests |
| T11 | SQL injection through filters, sort or search | database | allow-listed columns and operators, bound parameters, opaque keyset cursors validated on decode (values bound, never interpolated) | `test_ws4_views.py` |
| T12 | Provider names, credits or cost leak to the client | client output | `scripts/scan_client_output.py` on every tenant-visible output; usage in units; audit export by category | WS4, WS5, WS7 tests |
| T13 | Connector over-reach (wider scope, folders not named, writes) or token leak | customer systems | exact scope match, resolved Drive ancestry, quarantine, no write call, tokens only as secret references, token canaries | `test_ws6_connectors.py` |
| T14 | SSRF through IdP or connector URLs | network | SSRF-guarded client (no private, link-local, loopback, reserved or multicast addresses; redirects off); egress proxy [VERIFY] | WS2 port tests |
| T15 | Malicious branding upload (polyglot, decompression bomb, SVG script) | web app, exports | sniffed PNG or JPEG only, caps before decoding, re-encoded, scanned | branding tests |
| T16 | Plan limits bypassed by concurrency | billing | one conditional update per check | 100-way race test |
| T17 | Deletion destroys ledgers or audit, or one person deletes a tenant | data integrity | two-person approval after a dry run; never-delete list; bounded resumable passes | `test_ws1_*`, `test_ws8_admin.py` |
| T18 | Denial of service through large exports, long SSE streams, huge assertions | availability | page sizes, stream max duration and heartbeat, assertion size cap, fetch bounds | WS4, WS5, WS2 tests |

Residual risks (not proven here): real IdP behaviour, real provider scopes, production egress,
load, and the external penetration test (`PENTEST_SCOPE.md`).
