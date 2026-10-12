# Front end to backend gaps

These are capabilities the client and operator screens need that the API does not offer yet.
The front end does not fake them. Each screen is built to the contract it needs, and the
affected parts are hidden until the backend supplies the data. `FIX_PROMPT.md` is not in the
repo, so the "Suggested backend item" column describes the work instead of citing an id.

| Id | Screen | Missing | User impact | Suggested backend item |
|---|---|---|---|---|
| G-01 | Header, Add account | Per-user account cap and usage. Only the tenant plan limit `me.plan_limits.accounts` exists, and the count is taken from `GET /v1/accounts?limit=200`. | The meter shows the workspace limit, not a per-user one, and counts above 200 would be wrong. | `GET /v1/me/cap` returning `{used, limit}` per user, enforced on `POST /v1/accounts` |
| G-02 | Account workspace | The one-minute brief, as text with citation markers. | The workspace shows "What we know" (top graded facts from the facts table) instead of a narrative. | `GET /v1/accounts/{domain}/brief` with prose plus `[n]` markers and an evidence list |
| G-03 | Account workspace, Conversation | Scoping an answer to one account. `NewTurn.account_id` exists but no endpoint returns an account's id; `AccountItem` and `AccountDetail` only carry the domain. | A question asked under an account goes to the whole workspace. | Add `account_id` to `AccountItem` and `AccountDetail`, or accept `domain` on `NewTurn` |
| G-04 | Conversation | Citations in the answer stream: fact ids, grades and source records. | Answers render without chips and without the evidence drawer. The components are built and tested (`/__design`). | A `citations` frame on the turn stream: `{n, fact_id, grade, reason, title, source_name, retrieved_at, excerpt, span, url}` |
| G-05 | Conversation, Deep dive | The grade's one-sentence reason and the supporting excerpt span on facts. | The drawer could show only the grade label. | `reason` and `evidence_span` on facts table rows |
| G-06 | Conversation | The "What this answer used" record: local facts and dates, web scope, mail and drive. | The used panel is hidden on live answers. | A `used` frame at stream end, persisted on `ChatTurn` |
| G-07 | Conversation, Refresh | A web refresh offer when a question needs newer data, with scope, time and credit estimates. | No inline offer. Refresh from web is still available from the account. | A `web_offer` frame on the stream, and `POST /v1/runs/estimate` |
| G-08 | Refresh confirmation | Scopes (job postings only, people and teams, full) with time and credit estimates, and credits left in the monthly pool. | The dialog offers the existing depths (standard or deep) and the pursue-client option, with no estimate. | `scope` on `RunBody`; `GET /v1/credits/pool` for client-safe pool status |
| G-09 | Schedule | Per-account refresh schedules, next run, last result, skipped runs with a reason. | No schedule screen. | `GET/PUT /v1/accounts/{domain}/schedule` |
| G-10 | Accounts, Add account | The 6th Library: accounts assigned to this client, the "From 6th Library" tag, and free local add. | No Library tab and no tag. | `GET /v1/library`, `POST /v1/library/{id}/add`, and `source` on `AccountItem` |
| G-11 | Accounts | A "what changed" count and schedule per account in the list. | The rows show the research state and date only. | `changes_since_seen` and `schedule` on `AccountItem` |
| G-12 | Shell, states | Idle-expiry status (warning 3 days before) and the terminated state with a contact. | No expiry banner or access-ended screen. | `me.expiry: {expires_at, status, contact}` |
| G-13 | Connections | Per-user read-only mail and drive limited to the user's accounts. `/v1/connectors` is tenant-level and admin-managed. | The Mail and drive deep dive link and the Connections screen are hidden. | A `connections` capability flag and per-user connector endpoints |
| G-14 | All client screens | A flat client role. The backend still has admin, analyst, seller and viewer roles. | The UI mirrors the server: viewers do not see Add account or Refresh from web. | A product decision first, then role removal in the guard |
| G-15 | Operator screens | Aggregates with question categories, the nightly isolation check result, per-client expiry, cap and pool settings, library upload and publish, platform defaults and a skill version selector. | The operator console is not rebuilt in this pass. | See the build prompt, section 9 |
| G-16 | Home | Signals carry `account` as a string; whether it is always the domain is not stated in the schema. | A signal row links to `/accounts/{account}`. If the value is not a domain, the link shows the not-found state. | Document `SignalEvent.account` as the domain, or add `domain` |
| G-17 | Copy rules | The client-safe denylist (`DENYLIST_PATTERNS`) rejects "N credits" and "credits remaining", while the build prompt asks client screens to show credit estimates and the credits left. | Credit figures are designed in the web offer (gallery only) but cannot ship until this is settled. | A decision for the owner |
