# Build plan: final product

This plan replaces the root `PLAN.md` (the research-files plan, now superseded: `research_files` is deleted in favour of `documents`). The spec is `docs/build/BUILD_PROMPT.md`; the starting state is in `UNDERSTANDING.md`. Work happens on branch `feat/final-product`.

## Owner decisions recorded during the build (2026-10-13)

| Decision | Effect |
| --- | --- |
| 1 metered unit = 1,000 model tokens. The default checkpoint stays 1,000 units. | A deep run pauses once or twice. Both numbers are settings. |
| Tables that hold data are exported to CSV, then dropped | A script writes each table to a gitignored folder before the drop migration runs. |
| `BUILD_PROMPT.md` wins over `docs/build/CHANGE_CLAUDE_ONLY_SEARCH.md` | Keep the five data tools (Parallel, Firecrawl, PredictLeads, BitScale, Sumble), Normal and Deep modes shown as the "Research: off/on" chip, and the model picker with Kimi K3. The change file is not built. |
| The visual pass (pastel, 3D, motion) was rejected and reverted | Section 8 of the build prompt (minimal) stands. `docs/build/VISUAL_PROMPT.md` and `VISUAL_NOTES.md` are records only. |

## My decisions (each one is reversible)

| Topic | Decision | Reason |
| --- | --- | --- |
| Data grid | `@tanstack/react-table`, already a dependency | Headless: it renders a plain `<table>`, so section 8 still applies. Server-side sort, filter and keyset paging stay in the API. |
| CRM (`connectors_client/crm_*`, `crm_writeback/`, `/v1/crm`) | Delete | Not in the spec. Its tables are empty. Reading CRM data is covered by uploading an export in file mode. |
| Tenant's own model key (`/v1/chat/provider-key`) | Delete | No source document mentions it. Model keys are platform settings. |
| Embedder | Keep `HashingEmbedder` behind the existing `Embedder` port, selected by a setting | Only an Anthropic key exists, and Anthropic has no embeddings API. A real embedder needs a second sub-processor with zero retention (Arch 14.3), which is a decision for the owner. |
| Audio | Rejected with a plain message | No transcription vendor is configured. |
| Idle-access expiry | Optional per-client setting, default off, labelled access control | As the spec allows. Whether to keep it is flagged. |
| `/v1/runs/{id}/collected` | Keep | It shows what a stopped run kept, which the "stop keeps everything" rule needs (AUDIT A11). |

## Order of work

Each step ends with the affected tests green and its own commit(s).

1. **(b) One company run completes on the hermetic stack.**
   - The Word Summary builds (`missing_narrative`).
   - The gate runs and can pass. The `node` renderer is not the default.
   - Fix OI-TRK-2 (`lead_verification` with no leads), OI-EXP-2/3/4/6 (links registered as sources) and the `unconfirmed_stated` wording rule.
   - Chat works with the fake model (`api/main.py`).
2. **(c) Remove caps.**
   - Delete the wallet, plan limits, seats limit, spend caps, throttle and chat turn limit.
   - The ledger keeps recording but never blocks.
   - The drop migration runs after the CSV export.
3. **(d) Skill registry and executor.**
   - Add `skill_versions` and a loader from `skills/abm-account-research/`, with a fixture skill in `tests/fixtures/skills/`.
   - Build the executor: an agent loop with `read_skill_file`, data tools through the ledger and cache, a quarantined extractor for untrusted text, and skill scripts run in a no-network sandbox from a step-to-script runtime table.
   - Add the state machine on `account_runs`.
4. **(e) Meter and checkpoints.**
   - Add `run_checkpoints`, the rate table and `POST /v1/runs/{id}/checkpoint`.
   - Auto-continue is stored on the run.
   - Paused runs wait indefinitely; the 24h timeout goes.
   - Run events stream over SSE.
5. **(f) Chat.**
   - `POST /v1/chat` takes `mode` normal or deep. Normal never calls the web or a broker (asserted).
   - Add the model picker (`GET /v1/models`), the Kimi K3 provider behind the model-provider interface (Moonshot docs looked up, not guessed), and the per-client "Allow non-Claude models" setting (default off).
6. **(g) File mode.**
   - `/v1/files/*` is backed by `documents`. `research_files` is deleted, after its shared code moves into `documents`.
   - Facts from files are Internal. Add the company choice, the two-output rule with a canary test, and a prompt-injection file test.
7. **(h) Client screens:**
   - Ask with the Research chip and model chip
   - Accounts
   - New research
   - Run
   - Account with its tabs and Files
   - global error pages
   - CSP nonce
   - mobile menu overlay
   - per-page titles
   - every A6 fix
8. **(i) Admin console:** five pages (Overview, Clients, Users, Runs, Settings) plus the missing operator users and runs endpoints.
9. **(j) Deletions:**
   - Workflow v1, the ABM graph and legacy `runs`, removed after acceptance tests 1 to 3 pass
   - connectors_client, research_files and the remaining legacy web files
   - root clutter
10. **(k) Tests and docs:**
    - Playwright with axe at 1280px and 375px
    - all 15 web test files with no exclude list
    - `pytest-timeout`
    - README, STATUS, TESTING, BUILD_LOG and SCREEN_MATRIX corrected
    - the final report

## Blocked until the owner supplies something

- **Acceptance test 1 and the skill-version part of test 4** need the real skill package at `skills/abm-account-research/`.
- **The real-run cost report** needs `ANTHROPIC_API_KEY` and the Firecrawl, BitScale, Sumble and PredictLeads token keys.
- **Real tool prices** (today they are $0.25 placeholders) and the Kimi K3 key and price.
