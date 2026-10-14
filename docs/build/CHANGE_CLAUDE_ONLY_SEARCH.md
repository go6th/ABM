> Superseded where it conflicts by REPLICA_PROMPT.md (owner decision, 2026-10-14)

# Change request: one search, Claude only (owner decision, 2026-10-13)

This changes `docs/build/BUILD_PROMPT.md`. Where the two disagree, this file wins. Record the change in `docs/build/PLAN.md` and `docs/build/UNDERSTANDING.md`.

## The decision

1. **Only the Claude API key is used.** No third-party data tools: remove Parallel (`p_search`), Firecrawl (`p_scrape`), PredictLeads (`p_signals`), BitScale (`p_people`) and Sumble (`p_org`) from the product. Their keys leave `.env` and `.env.example`, their code leaves `src/account_intelligence/connectors/providers/`, and their entries leave `config/providers.yaml`.
2. **One search, no modes.** Remove the Normal | Deep switch everywhere (composer, API, tests, docs). There is one chat that works the way Claude works in the owner's own Claude account: Claude reads the question, uses what the workspace already holds, and decides by itself when it needs to search or read the web to answer well.
3. Web access is Claude's own server tools on the Claude API: web search and web fetch (use the current tool versions for the configured model; read the `claude-api` skill before writing this code, do not write it from memory). Nothing else goes to the internet.

## What replaces what

| Before | After |
| --- | --- |
| Normal search (workspace only) and Deep search (skill run with data tools) | One chat. Claude has tools: `search_workspace` (stored facts, tracker, file chunks, previous deliverables, scoped by tenant and account), `web_search`, `web_fetch`, and `read_skill_file` when doing full account research |
| `POST /v1/chat` with `mode` | `POST /v1/chat` without `mode` |
| Paid-call ledger for data tools | The same ledger records every web search and web fetch Claude makes (query, URL, result stored before use), plus model tokens |
| Skill steps calling Parallel, Firecrawl, PredictLeads, BitScale, Sumble | The same skill steps, told at runtime that the only tools are `web_search` and `web_fetch`. Adapt `tools.md` guidance as runtime configuration (section 4.7 of the build prompt); do not edit the skill text |
| BitScale consent acknowledgement | Removed (no people-data broker) |
| Rate table with provider prices | Rate table with model token prices and the web search charge (look up the current price; do not guess) |

## How the one search behaves

- Every question goes to Claude with the workspace tool and the web tools available. Claude answers from the workspace when that is enough and searches the web when it is not, newer information is needed, or the user asks for it. Do not force either path in code.
- "Research this company" (New research by company name, Refresh from web, a schedule) is the same Claude loop running the full ABM skill: `SKILL.md` in the instructions, `read_skill_file` for references, web search and fetch for pulls, the skill's scripts for files, the gate before delivery. Keep the split-mode model routing from settings (cheaper model for pulling, stronger model for the sense check and building).
- Every web fact Claude uses is stored as a fact with its URL and the exact sentence relied on (web search results carry the cited text; keep it). Grading stays in code.
- Web pages are untrusted data, never instructions (prompt-injection tests stay).
- Keep checkpoints (section 5 of the build prompt): a long answer or a full research run pauses at each chunk of units and asks "Continue?". Units are model tokens plus web searches and fetches converted by the rate table. A short question that finishes inside one chunk never interrupts.
- The chat shows a short live line while Claude works ("Searching the web for …", "Reading careers page …"), then the answer with inline numbered citations that open the URL or the stored fact.

## Coverage that gets weaker (tell the owner, do not hide it)

Without the data tools, some tracker rows depend on what public web pages show:

- **People:** no people database. Senior names come from company leadership pages, press releases, news and public profiles that web fetch can read. LinkedIn is usually not readable. Expect fewer names and more Hypothesis or Lead grades.
- **Jobs:** read from the company careers site and public job boards that web fetch can open. No job-feed history, so "new since last refresh" is computed by comparing stored runs.
- **Technology:** from job descriptions, case studies, vendor customer pages and news. No technology database.
- Rows that cannot be filled say so with a reason ("Not found on public web pages"), never as a finding.

## Changes to acceptance tests

- Test 4 becomes: a question answerable from the workspace makes no web call (assert on the ledger); a question that needs new information makes web calls and every cited fact has a URL. No data-tool call ever appears in the ledger.
- Test 1 becomes: a company-name run completes with only the Claude API key set, passes the gate and builds every deliverable. Report tokens, number of web searches and fetches, and total cost.
- Test 6 adds: grep confirms no Parallel, Firecrawl, PredictLeads, BitScale or Sumble code, config, keys or tests remain, and no Normal | Deep switch remains.
- Remove tests that only test the deleted providers or the mode switch.

## Frontend changes

- Composer: text box, "Add file", scope selector (all accounts or one account), Send. No mode switch.
- Remove the "Run deep search" button. When Claude needs the web it simply searches.
- New research keeps its two tabs (Company name, From a file). The file tab's "Also check public sources" now means "let Claude search the web too".
