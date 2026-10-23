# Google OAuth verification kit (Mail and drive)

Everything to paste into the Google Cloud console when you submit the Mail and drive app for
verification. Gmail and Drive read scopes are **restricted**: until the app is verified, only
listed test users can connect, and their access lapses every 7 days.

Steps that only the owner can do (they need your Google account): create the consent screen and
OAuth client, submit for verification, and book the security assessment. Everything below is
ready to copy.

## 1. Before you submit

| Requirement | Where it is |
|---|---|
| A domain you own, verified in Google Search Console | e.g. `app.6th.ai` (staging today is on `sslip.io`, which cannot be verified) |
| Home page | `https://DOMAIN/` (the sign-in page) |
| Privacy policy on the same domain | `https://DOMAIN/privacy` (fill the bracketed values first; counsel reviews) |
| Terms of service | `https://DOMAIN/terms` |
| Authorised redirect URI | `https://DOMAIN/v1/mail-and-drive/callback` |
| App logo (120x120 PNG) | export from the product logo |
| Support and developer contact emails | owner's choice |

Run `uv run --frozen python scripts/maildrive_setup.py check --env staging` once deployed: it
must print READY before you submit.

## 2. Scopes and justifications (paste per scope)

**`https://www.googleapis.com/auth/gmail.readonly`**

> Account Intelligence helps a salesperson prepare for conversations with the companies they
> sell to. When the user connects Gmail, the app reads only message metadata (sender,
> recipients, subject, date) and the short snippet Gmail supplies, for the last 30 days and then
> incrementally. It keeps only messages exchanged with the companies the user researches
> (matched by email domain or company name) and discards everything else immediately. Kept items
> are shown only to that user inside the app, to tell them when they last spoke with an account
> and to let answers cite it. The app never sends, modifies or deletes mail, never reads message
> bodies or attachments, never shares the data with other users, and never uses it for
> advertising or to train AI models. A narrower scope (gmail.metadata) does not return the
> snippet the user needs to recognise the conversation; no write scope is requested.

**`https://www.googleapis.com/auth/drive.readonly`**

> When the user connects Google Drive, the app lists file names, owners and modified dates to
> show which documents relate to the companies the user researches, and, only when the user
> picks a file in the app's file picker, downloads that one file so its text can be used for the
> user's own research. It never modifies, deletes or shares files and keeps nothing for files
> that do not match. `drive.file` is not sufficient because the user's existing files (not
> created by this app) must be found and matched; no write scope is requested.

## 3. Demo video script (unlisted YouTube link, about 3 minutes)

1. Show the browser address bar on `https://DOMAIN` and sign in as a test user.
2. Open **Mail and drive**. Point at "Read-only. Matched items are shown only to you".
3. Click **Connect** on Gmail. Show the full Google consent screen, including the app name and
   the `gmail.readonly` scope, then **Allow**. Back on the page it shows **Connected**.
4. Click **Sync now**. Show "items matched to your accounts", then open the matched account and
   its private mail-and-drive sheet with the matched email's subject.
5. Ask a question about that account and show the answer citing the email.
6. Repeat 3 to 4 for Google Drive, and show the file picker in **New research**.
7. Click **Disconnect** on Gmail, confirm, and show the items are gone.
8. Open `https://myaccount.google.com/permissions` and show the app's access was removed.

## 4. Security assessment (CASA)

Restricted scopes need an annual third-party CASA assessment (Tier 2 for most apps). What the
assessor will ask for, and where it lives in this repository:

| Topic | Evidence |
|---|---|
| Token storage | refresh tokens in Secret Manager, one secret per connection, never in the browser or a model (`src/connectors_client/oauth.py`) |
| Least privilege | read-only scopes only; `scripts/maildrive_selfcheck.py` fails on any other scope |
| Data minimisation | metadata and snippet only; unmatched items discarded (`src/connectors_client/maildrive.py`) |
| Per-user isolation | row-level security, owner-only (`migrations/0063_private_workspaces.sql`) |
| Deletion | Disconnect, feature off, or access ended revokes and deletes (`src/connectors_client/lifecycle.py`) |
| Access logging | every token exchange, refresh and list call in `connector_access_log` |
| Transport and hosting | HTTPS only, Cloud Run behind a managed-certificate load balancer |
| Tests | `tests/replica/test_maildrive_e2e.py`, `tests/connectors/test_maildrive_core.py` |

## 5. Microsoft (Entra) publisher verification

Not required for the app to work, but without it Microsoft shows "unverified" on the consent
screen and many customer tenants block user consent. Verify the publisher with a Microsoft
Partner Network ID in the app registration's Branding page. `Sites.Read.All` (SharePoint) needs
admin consent in most customer tenants; Outlook (`Mail.Read`) and OneDrive (`Files.Read`) do not.
