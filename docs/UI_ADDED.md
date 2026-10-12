# UI added in the light and dark rebuild

Each row is a capability the backend already served with no screen, or a FIX to a screen that
did not work. Every operation below is in `docs/api/openapi.json`; no endpoint was added and no
backend behaviour changed.

## BUILD: capabilities that had no UI

| Screen | What it does | Operations |
|---|---|---|
| `/downloads` (new, "Files" in the navigation) | Pick an account; see its Word, Excel and PDF packages, newest first, with file names and sizes, and download them | `GET /v1/exports?domain=`, `GET /v1/exports/{id}/files/{n}` |
| Account page, Downloads | Earlier packages load with the page (one per kind), so files built before are not lost on reload; "All files" opens it | `GET /v1/exports?domain=` |
| `/feedback` (new, Settings, admins and `usage:read`) | First-call question use and Confirmed-fact reference rates for the window, and the feedback people left on rows and runs | `GET /v1/usefulness/metric`, `GET /v1/feedback` |
| `/settings/sso` | Add a connection (OpenID Connect or SAML) and edit one at its version: issuer, client, endpoints, certificate, groups to roles, verified domains, just-in-time, admin multi-factor, active | `POST /v1/sso/connections`, `PUT /v1/sso/connections/{id}` |
| Deep dive tables | Save a view in a dialog (no `window.prompt`), rename the selected view, archive it | `POST /v1/views`, `PUT /v1/views/{id}`, `POST /v1/views/{id}/archive` |
| `/runs/{id}` | The spend cap proposal the run waits on: amount, planned work by category, unknown-cost and exclusion warnings; approve or reject that exact proposal (version and hash) | `GET /v1/runs/{id}/cap`, `POST .../cap/approve`, `POST .../cap/reject` |
| `/runs/{id}` | Live work so far, by category, while the run is not finished | `GET /v1/runs/{id}/spend/live` |
| `/settings/connectors` | Allow a Drive folder, a CRM object or a CRM field; fetch now (one bounded pass, counts only) | `POST /v1/connectors/{id}/grants`, `POST /v1/connectors/{id}/fetch` |
| `/usage` | Download the usage CSV | `GET /v1/usage/export` |
| `/alerts` (admins) | How useful delivered alerts have been: delivered, rated and the share rated useful, with the small-sample note | `GET /v1/alerts/usefulness` |
| `/operator/tenants` | Two-person deletion: open a request with a reason, dry run, approve as a second operator (the request ID is shown so they can continue it), execute bounded passes after typing the tenant ID | `POST /operator/v1/tenants/{id}/deletion`, `.../dry-run`, `.../approve`, `.../execute` |
| `/operator/runs` | Pre-pulled data for a run: open an upload intent, show the file spec and the signed upload address, record completion with the account confirmation and pull date | `POST .../prepulled/intents`, `POST .../prepulled/{intent}/complete` |
| Every screen | Light, Dark or Match system, kept in the `ai_theme` cookie and rendered on the server | none (UI only) |

The pre-pulled upload does not send the file from the browser: the CSP allows connections to
this origin only, and widening it is a security change. Staff upload with their own tool to the
signed address the page shows.

Not built, because they need a decision or a capability that does not exist: the pull option in
the Refresh dialog (`GET /v1/runs/pull-option` exists, but offering "Phase 2 only" changes what
a client is charged for, which is the owner's call), and "View as a client".

## FIX: screens that did not work

| Screen | Problem | Fix |
|---|---|---|
| `/chat` | The thread list loaded when a thread was created, before its first turn had a title, so the new thread's button had an empty name | The list reloads when each turn ends; an untitled thread reads "Untitled conversation" |
| Deep dive tables | An empty table showed a bare header row | An empty state under the table, worded for "no rows" or "nothing matches" |
| `/reset` | A link without its token only disabled the button | It says the link is incomplete or expired and links to a new request |
| `/operator/login` | An async page was marked as a client component (React warns and refuses it) | A server component |
| Run, team, connectors, alert preferences | `window.confirm` for destructive steps | A confirmation dialog |
| `/settings/model-key` | Named a model vendor on a client screen | "Model provider key" |
