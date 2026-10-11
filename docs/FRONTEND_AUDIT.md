# Front end screen audit (2026-10-11)

This is the audit of the 16 screens in the front end build prompt, taken before and after the
first pass. It lives here instead of `BUILD_LOG.md` because that file has uncommitted changes
from another session working in this folder.

The pass started from Next 16.4 (App Router) with React 19, Radix and lucide icons, npm,
Vitest and Playwright, and a generated zod client from `docs/api/openapi.json`.

## Client screens

| # | Screen | Before | After this pass | Files |
|---|---|---|---|---|
| 1 | Home | PARTIAL: stat tiles, an orb hero, no question box | DONE except G-01 | `app/(client)/page.tsx`, `components/ws/HomeAsk.tsx` |
| 2 | Accounts | PARTIAL: plain list | PARTIAL: rows, search, sort and add are done; the library tag, change count and schedule wait on G-10 and G-11 | `app/(client)/accounts/page.tsx`, `components/ws/AccountList.tsx` |
| 3 | Account workspace | PARTIAL: run form and table links | PARTIAL: done except the brief (G-02) and account scoping (G-03) | `app/(client)/accounts/[domain]/page.tsx` |
| 4 | Conversation | PARTIAL: chat with a model picker | PARTIAL: streaming, stop, copy, download and threads are done; chips, the used panel and the web offer are built but wait on G-04, G-06 and G-07 | `components/ws/Conversation.tsx`, `components/ws/Conversations.tsx` |
| 5 | Deep dive | PARTIAL: generic table pages | UNCHANGED: links and nav are new, the tables keep the old look | `app/(client)/accounts/[domain]/tables/[table]/page.tsx` |
| 6 | Refresh confirmation | PARTIAL: inline run form | PARTIAL: dialog, idempotent confirm, progress by slices and cancel are done; scopes and estimates wait on G-08 | `components/ws/RefreshDialog.tsx`, `components/ws/RunStatus.tsx` |
| 7 | Schedule | MISSING | MISSING (G-09) | |
| 8 | Add account | PARTIAL: domain field | PARTIAL: domain add and cap are done; the 6th Library waits on G-10 | `components/ws/AccountList.tsx` |
| 9 | Connections | PARTIAL: admin integrations | MISSING per user (G-13), hidden | |

## Operator screens

| # | Screen | State | Notes |
|---|---|---|---|
| 1 | Overview | PARTIAL | `app/operator/(console)/page.tsx`. The other session was editing it during this pass. |
| 2 | Clients | PARTIAL | `app/operator/(console)/tenants/page.tsx` |
| 3 | Client detail | MISSING | G-15 |
| 4 | Runs | PARTIAL | `app/operator/(console)/runs/page.tsx` |
| 5 | Usage | PARTIAL | `app/operator/(console)/cost/page.tsx` and `spend/page.tsx` (being edited by the other session) |
| 6 | Library upload | MISSING | G-15 |
| 7 | Platform defaults | PARTIAL | `app/operator/(console)/configuration/page.tsx` (being edited by the other session) |

## Decisions made in this pass

- **Settings moved:** settings and admin pages moved out of the main rail into a grouped
  settings navigation, so the rail stays within seven items.
- **Provider key moved:** the provider key panel moved from the chat page to
  `/settings/model-key`. Its label names the model vendor; the client copy rules forbid vendor
  names on client screens, so the owner should decide whether that holds for an admin settings page.
- **Run options dropped:** the old run form's lane toggles and phase-only modes are not in the
  new refresh dialog. They are still available through the API.
- **Tenant accent dropped:** the branding accent override is not applied to the new workspace,
  which uses one fixed accent, as the design direction asks.
