# Front end to backend map

The browser calls only its own origin. `/v1/*` is proxied to the API by `web/app/v1/[[...path]]/route.ts`,
so the session cookie stays first-party. Server Components call the API directly with
`web/lib/server-api.ts`, which forwards the cookie and a request id. Both paths look the
operation up in the generated contract (`web/lib/gen/api.gen.ts`, generated from
`docs/api/openapi.json` by `scripts/gen_web_client.py`) and refuse any operation that is not in it.
Mutations carry `X-CSRF-Token` from `GET /v1/sso/session`.

The rebuilt screens:

| Screen | Operation | Side | Errors handled |
|---|---|---|---|
| Shell (all client screens) | `GET /v1/me`, `GET /v1/accounts?limit=200` | server | 401 sends the user to sign in with a return path; other errors throw to the error boundary |
| Home | `GET /v1/accounts`, `GET /v1/signals` | server | 5xx: retry card with a reference |
| Home, Conversations, Account | `POST /v1/chat/threads`, `POST /v1/chat/threads/{id}/turns/stream` (SSE: `text`, `done`, `error`) | browser | 429 hits the usage limit, 422 or `out_of_context` is out of scope, 503 means unavailable, anything else fails with retry; Stop aborts the stream |
| Conversations | `GET /v1/chat/threads`, `GET /v1/chat/threads/{id}`, `DELETE /v1/chat/threads/{id}` | browser | A thread that fails to load returns to a new conversation |
| Accounts | `GET /v1/accounts`, `GET /v1/me`, `POST /v1/accounts` | server, then browser to add | 402, 409 or limit: cap copy; 422: invalid domain; else failed |
| Account workspace | `GET /v1/accounts/{domain}`, `GET /v1/accounts/{domain}/tables/facts`, `GET /v1/accounts/{domain}/history`, `GET /v1/me`, `GET /v1/settings` | server | 404 shows the same "Account not found" page for a missing or foreign account; a 409 `no_finished_run` on facts is an empty state; a 403 on settings drops the pursue-client option |
| Refresh from web | `POST /v1/runs` with an `Idempotency-Key` | browser | 409: a run is already in progress; 402, budget or wallet: pool exhausted; else failed |
| Run progress | `GET /v1/runs/{id}/progress` (polled every 4s), `POST /v1/runs/{id}/cancel` | browser | Errors keep the last state |
| Downloads | `POST /v1/exports`, `GET /v1/exports/{id}/files/{n}` | browser | A package that is not delivered shows a plain line |
| Settings, provider key | `GET`, `PUT` and `DELETE /v1/chat/provider-key` | browser | Admins only; the server refuses others |

Unchanged screens still call the API through `web/lib/api.ts`: the deep dive tables, history,
alerts, runs and settings pages. Gaps are listed in `docs/FRONTEND_BACKEND_GAPS.md`.

## Local development

```
# API on 8088, accepting the dev web origin for credential changes
PUBLIC_BASE_URL=http://localhost:3001 uv run --env-file .env uvicorn account_intelligence.api.main:app --reload --port 8088
# web on 3001, proxying /v1 to it
cd web && API_ORIGIN=http://localhost:8088 npx next dev -p 3001
```

Without `PUBLIC_BASE_URL` set to the web origin, sign-in answers 403 ("same-origin request
required"), because the API checks `Origin` on credential changes.
