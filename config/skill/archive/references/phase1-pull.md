# Phase 1 runbook: data pulls (split mode)

Use this file only when the user asks for a split run ("abm pull", "phase 1", or pulls on one model and analysis on another). The checklist, the tools playbook and every standing requirement still apply in full. Read `checklist.md` and `tools.md` before the first tool call, exactly as in a single model run.

This runbook is written for a model that follows rules and does not fill gaps. Every step therefore has three parts: the call to make (with parameters), the result to expect, and what to do when the result is outside that range. Do not skip a part because it seems obvious. Do not infer a rule that is not written here; if a situation is not covered, record it in the pull log and continue.

## Purpose

Collect everything phase 2 needs into files, with proof of every query, and nothing else. Phase 1 over-collects: when in doubt, pull it. Phase 1 never interprets.

## Phase 1 does not

- Read postings for meaning, classify them, or judge relevance.
- Grade anything, write narrative, hypotheses or tracker text.
- Exclude people or assign priority. Keep every row; tag security, compliance and audit titles in a column so phase 2 can exclude them.
- Build any deliverable.
- Summarize its own findings for phase 2. Phase 2 starts from raw files only.

## Phase 1 must capture (so phase 2 can judge)

Phase 1 does not decide what belongs to the account, but it records what phase 2 needs to decide: the domain or organization id each record came from; every entity, affiliate or location label carried in a posting or a feed row, kept verbatim; the parent and subsidiary domains the feed reports; the current employer field on every profile; and, when a search returns records in an unexpected city or under an unexpected legal name, a line in the pull log saying so. Never drop a record on suspicion; tag it.

## Standing rules that apply to every step

- **Log the exact parameters of every paid call.** Not a summary ("31 title variants") but the parameter values as sent. When a call underperforms, the log must show what was sent so the query can be fixed.
- **Lane keywords** on every account: Salesforce, MuleSoft, ServiceNow, SAP, Tableau, Veeva, Workday, plus any platform the account is known for. **Never ServiceMax, Siebel or Databricks**: not as a search term, a technology filter, a title variant, a live board query or a keyword count. If a feed row or a tech stack call returns them unasked, keep the row as returned; never query on them.
- **Neutral labels in the pull log.** Name tools in the log by role, never by vendor: page scraper, job and news feed, title search tool, association search tool, open web search. The log travels in the workspace zip to the client. Data files follow the same rule in every `source` field: "6th agents".
- **One account per file set.** When a feed row, release or search result names another account in this ABM program (any account with its own folder in the ABM Research Drive), keep the row and replace that account's name with "[another program account]". Log that a redaction was made and which file. Before packaging, grep every data file for the names of the other program accounts and fix any miss.
- **Never construct a URL** for a careers board, ATS tenant, newsroom or vendor page. Visit, follow links, record the URL that answered.
- **Never set file_name** on any association search tool call. Results come back inline and are saved locally.
- **Credit cap.** One cap for the run, agreed at step 0. Stop and report at 80 percent of the cap. Below that, run without stopping to ask.
- **Spend priority inside the cap.** When the cap forces a choice, spend in this order: (1) people completeness: every title search row and every gap check row with a full profile; (2) structured feeds, confirmed stack and teams; (3) releases, media and searches. Never cut a profile pull to buy more media pages; a thin news stub can be fetched in phase 2 for a few credits, a missing profile cannot be reasoned around.

## Sequence

### 0. Setup (free)

Call: balance on every data tool (page scraper, job and news feed, title search tool, association search tool). Confirm account name, domain and the run's credit cap. Start `pull_log.md` with the balances table.

Expect: a number for every tool. If a tool returns no balance, log "balance not reported" and continue.

### 1. Pass 1, internal history

Call: mail by company name and domain; call notes by company name; Drive only inside folders the user or client named. Write findings to `notes/pass1.md`. No recording links, no thread titles, no other account names.

If the run has no mail, calendar or Drive access (the usual case on the lower cost model), log "pass 1 skipped: no internal access in this environment; user runs it separately" and continue. Do not try to substitute web sources for internal history.

### 2. Company baseline and newsroom

Calls, in order:

1. Open web search: "[Account] company overview ownership leadership", "[Account] news 2025 2026", "[Account] appoints chief officer 2025 2026". Save queries and result counts to `data/web_searches.json`.
2. Page scraper map of the company site filtered on "news" (limit 300). Save the URL list to `data/site_map_news.txt`.
3. Page scraper: the executive leadership page. Save to `data/leadership.json` with name and title for every executive.
4. Page scraper: every release and leadership announcement URL from the map dated in the last 18 months. Save each to `data/releases/YYYY-MM-DD_slug.md`.
5. **Lane vendor partnership search, mandatory.** For each of Salesforce, MuleSoft, ServiceNow, SAP, Tableau, Veeva and Workday: open web search "[Account] [vendor] partnership OR partner OR agreement". Fetch any company release, vendor release or trade press article that names both. Save under `data/releases/`. Log each query even when it returns nothing relevant.
6. Open web search for the account's major identity, cloud, data and integration vendor announcements in the last 18 months ("[Account] selects", "[Account] deploys", "[Account] modernizes"). Fetch what is found.

Expect: 10 to 60 release and article files for a company of 1,000 to 5,000 people. Under 10: the site map missed the newsroom; search "[Account] press releases" and "[Account] newsroom" and map the newsroom URL directly.

Paywalled pages: search for a syndicated copy (wire services, Yahoo, trade press). Log the paywall and the copy used. Never guess at content.

### 3. Structured feeds (job and news feed)

Calls, with these parameters:

1. Company record by domain. Save to `data/company_record.json`. Record the parent domain and every subsidiary domain reported.
2. News events: domain, found_at_from = today minus 18 months, limit 1000. Save to `data/news_events.json`.
3. Technology detections: domain, last_seen_at_from = today minus 24 months, limit 1000. Save to `data/techstack_detections.json`.
4. Job openings: domain, active_only false, first_seen_at_from = today minus 12 months, with descriptions, limit 1000, page until the reported count is reached. Save to `data/jobs_12mo.json` with a `count_reported` field and a `count_saved` field that must match.
5. Connections: domain, partner, vendor, integration, other, limit 1000. Save to `data/connections.json`.
6. **Subsidiary domains, mandatory.** For every subsidiary domain reported in call 1, run job openings (12 months) and technology detections (24 months). Save to `data/subsidiary_domains.json` even when the result is zero rows. Log the zero.

Expect: postings 50 to 500 for a company of 1,000 to 5,000 people; detections 100 to 800. Under 20 postings: the domain is wrong or the company hires under another domain; check the careers board domain found in step 4 and re-run on it.

Count keyword hits on posting text for every lane keyword and save the counts in the log line. Databricks is not counted.

### 4. Careers site and live boards

Calls:

1. Page scraper: the company careers page. Record every board link found (a company often has one board per country or legal entity). Save the verified URLs to `data/live_board.json`.
2. **For every board found, not only the first:** scrape the board listing, then search it once per lane keyword (Salesforce, MuleSoft, ServiceNow, SAP, Tableau, Veeva, Workday, plus any platform the account is known for). Save the result count per keyword per board.
3. Scrape the IT or technology category page of every board, if the board has one. If the board filters in script only and has no category URL, log that.
4. Scrape every live posting that is not already in `data/jobs_12mo.json` (match on requisition id or title plus location). Save each to `data/releases/YYYY-MM-DD_live_REQID_slug.md`.

Expect: at least one board. If the careers page links to a second board (India, EMEA, a subsidiary), the run is incomplete until that board has been searched too. Zero results for a keyword is a result; log it.

### 5. Confirmed stack and teams (association search tool)

Calls:

1. Organization match by name plus domain, requesting id, slug, employee count, HQ and subsidiaries only. Save to `data/org_resolve.json`.
2. Confirmed tech stack for the organization id, all categories. Save to `data/techstack_confirmed.json` with vendor URLs stripped. This call returns everything the tool has, including platforms outside scope; keep the file as returned.
3. Technology lookups for the lane list only: Salesforce, MuleSoft, ServiceNow, SAP, Tableau, Veeva, Workday. Never look up Databricks, Siebel or ServiceMax.
4. Platform teams: organization id, technology in the lane slugs from call 3, activity since today minus 18 months, ordered by jobs count descending, limit 25, **6 paid attributes**: team name, jobs count, job function list, last activity, technology list (every technology on the team with its used flag and jobs count) and location list. Do not request job posts or related people; both return empty on every team (Availity, Oct 6 2026) and cost the same. Save to `data/teams.json` with vendor URLs stripped and a `total_matching` field.
5. Organization signals. Save to `data/org_signals.json` even if empty.

Expect: teams call around 150 to 175 credits at 25 teams and 6 attributes. If the estimate is above 200, the attribute count is wrong; fix it before calling.

### 6. People (title search tool)

Run the consent flow in `tools.md`. Then two Find People sources on the same grid, both with `q_organization_domains` = [account domain] and **no seniority filter and no department filter**. Seniority and department filters intersect with titles and cut the match count by 80 to 90 percent (tested Oct 6 2026 on availity.com: titles only 63 matches; the same titles plus seniority c_suite, vp, head, director: 5 matches).

**Source 1, leadership and platform titles.** `person_titles` exactly this list:

Chief Information Officer, CIO, Chief Technology Officer, CTO, Chief Data Officer, Chief Digital Officer, Chief Architect, Chief Product Officer, VP Engineering, Vice President Engineering, VP Technology, VP Information Technology, VP Data, VP Product, VP Platform, VP Enterprise Applications, VP Revenue Operations, VP Sales Operations, Head of Engineering, Head of IT, Head of Data, Head of Platform, Head of Analytics, Director of IT, IT Director, Director Information Technology, Director of Engineering, Director Enterprise Applications, Director Business Applications, Director Data, Director Analytics, Director Business Intelligence, Director Platform, Director Integration, Director Salesforce, Director CRM, Director ServiceNow, Director Product, Director Revenue Operations, Director Sales Operations, Director Enterprise Architecture, Enterprise Architect, Principal Architect, Solutions Architect, Salesforce Architect, Salesforce Administrator, Salesforce Developer, ServiceNow Administrator, ServiceNow Developer, MuleSoft, Tableau, Business Intelligence, Data Engineering, Integration

For a life sciences account add: Veeva, Vault, Commercial Systems, Commercial Operations, Commercial Excellence, Medical Affairs Systems, Regulatory Systems, Quality Systems, Clinical Systems. For an account with SAP in its stack add: SAP, ERP, S/4HANA, Master Data.

rows_to_load 200; pagination {page: 0, total_pages: 10}.

**Source 2, manager level.** `person_titles` = Manager, Senior Manager, Sr Manager, Program Manager, Product Manager, Technical Product Manager, Engineering Manager, IT Manager, Applications Manager, Salesforce Manager, CRM Manager, ServiceNow Manager, Analytics Manager, Data Manager, Integration Manager, Revenue Operations Manager, Sales Operations Manager, Enterprise Applications Manager. rows_to_load 200.

Read `matched_upstream` from the grid status for each source and log it with the full parameter list. Load every matched row (call pull latest on the source until the row count equals matched_upstream). Save name, title, headline, profile URL, current employer label and location for every row to `data/people_grid.txt`, plus a `security_tag` column set to true when the title contains Security, CISO, Compliance, Audit or Privacy.

Expect: source 1 between 30 and 150 matches at a company of 1,000 to 5,000 people; source 2 between 20 and 150. Scale by headcount: roughly 2 to 5 percent of employees for source 1. **Under 15 matches on source 1:** the query is wrong. Log the parameters, check that no seniority or department filter was sent, check the domain against the company record, rerun once with the corrected parameters, log both counts. Do not proceed to step 7 on a single digit result at a company above 500 employees.

### 6a. Gap check and merge (association search tool), before any profile pull

For each lane (Salesforce, MuleSoft, ServiceNow, SAP, Tableau, Veeva): if no row in `data/people_grid.txt` has a title naming that lane at Director or above, run one people search for that lane: organization id, technology = that lane slug, job_function in (Information Technology, Engineering and R&D, Product and Design, Revenue Operations), job level Senior Manager and above, attributes job_title, job_level, linkedin_url, location, limit 25. Log "not needed" for lanes that are covered. Save the raw result to `data/people_gapcheck.json`.

Then **merge every gap check person into the grid** as a new row (name, title, profile URL, lane searched, source "gap check"), deduplicating on profile URL first and name second. Add them to the title search grid as rows so the profile and posts columns in steps 7 and 8 run on them too. Record in the log: rows from title searches, rows added by gap check, rows after dedupe. Steps 7 and 8 run on the merged count, never on the title search count alone.

Expect: 25 to 100 credits per lane searched. Do not run a combined all lanes search; it cannot be attributed by lane in phase 2.

### 7. Full profiles

Call: the full profile enrichment column for every row in the grid. Estimate on 5 rows first, log the estimate, then run all rows. Save to `data/profiles_full.json` with every role, title, dates and the person's own description, verbatim.

Expect: a profile for 90 percent or more of rows. Rerun failed rows once.

### 8. Posts and comments

Call: recent posts and recent comments columns for every row whose title contains Chief, CIO, CTO, VP, Vice President, Head, Director, Architect, or names a lane platform (Salesforce, MuleSoft, ServiceNow, SAP, Tableau, Veeva, Workday). This is a mechanical rule so phase 1 needs no priority list. Save to `data/posts_comments.json`. Zero post rows are kept.

### 9. Gap check

Done in step 6a. Confirm here that the merged row count in the log matches the rows in `data/profiles_full.json` plus logged profile failures, and move on.

### 10. Person name and company searches (open web search)

Calls:

1. By person name for every C level, VP and any title naming a platform in `data/leadership.json` and `data/people_grid.txt`: "[Name] [Account] interview OR podcast OR speaker OR keynote". Log every query and result count.
2. By company name: "[Account] [vendor] customer story" for each lane vendor; "[Account] G2", "[Account] TrustRadius", "[Account] PeerSpot", "[Account] Gartner Peer Insights"; "[Account] Trailblazer", "[Account] AppExchange", "[Account] ServiceNow community"; "[Account] Medium", "[Account] Substack".
3. Event agendas: "[Account] HLTH OR HIMSS OR AHIP OR Dreamforce OR Knowledge 2026 speaker".

Scrape every agenda, speaker page, interview or customer story found. Save every query and count to `data/web_searches.json` and every captured page under `data/releases/`.

Expect: 20 to 40 queries. A page that returns 403 or 404 is logged as unavailable and retried once with a corrected URL from the search result, never with a guessed URL.

### 11. Former executive checks

Search by name every executive named in a release, news row or aggregator profile who is not in `data/people_grid.txt` and not on the current leadership page: "[Name] [Account]" and "[Name] joins OR appointed OR departs". Record the result verbatim (current, former, unresolved) with the evidence URL. Add to `data/web_searches.json`. Never resolve the status by inference; "unresolved" is a valid result.

### 12. Completeness gate, then package

Before zipping, check every line below and write the check results as the last section of `pull_log.md`. A line that fails is fixed before packaging, not handed to phase 2.

| Check | Pass condition |
|---|---|
| Balances | step 0 table present with a number or "not reported" for every tool |
| Pass 1 | `notes/pass1.md` present, or the skip line logged |
| Newsroom | `data/leadership.json` present; 10 or more files in `data/releases/`; one logged vendor partnership search per lane vendor |
| Feeds | `company_record.json`, `news_events.json`, `techstack_detections.json`, `jobs_12mo.json`, `connections.json`, `subsidiary_domains.json` all present; `count_saved` equals `count_reported` on jobs |
| Boards | every board link found on the careers page has keyword counts for all lane keywords |
| Stack | `techstack_confirmed.json`, `teams.json` (25 teams or `total_matching` if fewer), `org_signals.json` present |
| People | source 1 matched 15 or more (or the rerun is logged with both counts); every matched row loaded; gap check rows merged into the grid with the three counts logged; `profiles_full.json` covers 90 percent of the merged rows; posts pulled for every row the step 8 rule names |
| Gap check | one line per lane: pulled or "not needed"; merged rows carried into profiles and posts |
| Searches | `web_searches.json` holds 20 or more queries with counts |
| Scope | grep of all files for ServiceMax, Siebel and Databricks as query terms returns nothing in the log's query column |
| Other accounts | grep of all data files for every other program account name returns nothing |
| Tool names | grep of all files, the log included, for vendor tool names returns nothing |
| Parameters | every paid call in the log shows its parameter values |
| Credits | totals by tool and a run total against the cap |

Then zip `data/`, `notes/` and the log as `{Account}_phase1.zip`. File names are neutral from the start: no vendor prefixes.

## Pull log format

One line per call: step, tool by role, the parameters as sent, result count, credits, output file. Totals by tool at the end, then the completeness gate table. This log becomes the proof column of the coverage tracker in phase 2.

## Handoff

Deliver the phase 1 zip and nothing else. Do not deliver it to the client. If phase 2 returns a second pull list, run exactly that list, append to the same files and log, and re-zip.
