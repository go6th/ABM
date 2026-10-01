# 08 API, authentication and billing (ARCH 9, 18.7, 18.8; SAAS 6)

## Endpoints (path versioning, additive changes only inside v1, Sunset headers for deprecations)
| Method and path | Purpose | Roles | Notes |
| --- | --- | --- | --- |
| POST /v1/runs | Create a run | admin, seller | Requires `Idempotency-Key` (8 to 64 chars); returns 202 |
| GET /v1/runs/{id} | Status and progress | all | ETag; polling or webhook |
| POST /v1/runs/{id}/cancel | Cancel | admin, seller | Idempotent |
| GET /v1/accounts | List | all | Cursor pagination |
| GET /v1/accounts/{domain}/brief | Latest graded public brief | all | Public facts only |
| GET /v1/accounts/{domain}/brief/internal | Blended internal brief | admin, seller | Phase B; **never exported** |
| GET /v1/accounts/{domain}/facts | Facts with evidence | all | Filter by grade, attribute |
| GET /v1/accounts/{domain}/changes | Deltas since a time | all | Cursor pagination |
| PUT /v1/accounts/{domain}/watch | Alert rules and cadence | admin, seller | Validated against plan limits |
| POST /v1/knowledge/uploads | Presigned upload | admin, seller | Phase B |
| DELETE /v1/knowledge/documents/{id} | Delete a document | admin | Starts cascade |
| GET /v1/wallet | Balance and ledger | admin | Ledger paginated |
| POST /v1/wallet/topups | Start checkout | admin | Payment webhook idempotent on event id |
| GET /v1/audit | Audit entries | admin | Filter by actor, resource |
| POST /api/inngest | Orchestrator callback | machine | Signature verified; not public API |

Request body for `POST /v1/runs`: `domain` (pattern `^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9-]+)+$`) and `depth` (`standard` or `deep`). Responses: 202, 402 insufficient credits, 429 rate limited.

## Access control
| Capability | Admin | Seller | Viewer |
| --- | --- | --- | --- |
| View public brief and facts | yes | yes | yes |
| View internal blended brief | yes | yes | no |
| Create or cancel runs; configure alerts | yes | yes | no |
| Upload or delete private documents | yes | upload only | no |
| Manage users, SSO, billing, redaction policy | yes | no | no |
| Read audit log | yes | no | no |

Tokens come from an external IdP and carry `sub`, `tenant_id`, `role`. Verify signature, issuer, audience, expiry; **re-check membership in the database with a ~60 s cache** so removed users lose access within about a minute. **Tenant comes from the verified token, never from the request body, path or query.**

## Conventions
Errors are problem-details JSON (type, title, status, detail, request id). Idempotency keys are scoped per tenant and enforced by a **unique constraint**, not only a cache; replays return the original result. Rate limiting is a Redis token bucket per tenant and per user with endpoint cost weights (429 plus Retry-After). Redis keys are prefixed with tenant id and purpose and hold nothing authoritative or private.

## Billing: reserve, commit, refund
| Entry | When | Effect |
| --- | --- | --- |
| reserve | Run creation, **same transaction as the run row** | Balance reduced only if sufficient (one conditional UPDATE) |
| commit | Terminal state | Records actual cost (may be less than reserved) |
| refund | Terminal state | Returns reservation minus actual; full refund when the platform was at fault |
| topup | Payment webhook | Idempotent on provider event id |
| adjustment | Support action | Reason code and audit entry required |

Nightly invariants: balance equals ledger sum; no negative balance (CHECK); every terminal run has **exactly one** settlement. BUDGET_EXCEEDED commits up to the cap and refunds the rest; the customer sees partial results, not an empty failure. **Credits are priced by depth tier, not by raw tokens.** Clients see credits and categories (people, postings, news, pages read), never vendors. Pricing has two parts: a platform fee per active account per month plus metered credits; concierge runs add a per-account service fee. 6th keeps its own broker accounts in phase one (bring-your-own keys only from phase two).

## Things the starter code leaves for you to build
Settlement (commit and refund at the terminal state, including the FAILED path), the reconciler, the outbox or stuck-run query, the nightly invariant job, webhook signing, and the top-up handler are described but **not in the ARCH listings**. See `13-starter-code-and-known-defects.md`.
