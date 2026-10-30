# ABM account research: living checklist

Version 1.11, October 7, 2026.
Read this in full at the start of every run. Update it whenever the user corrects the process, and record the change in the log at the bottom.

## Contents

1. Standing requirements from the user
2. Scope defaults
3. Run steps
4. Coverage rows (these become the tracker in the Summary)
5. People rules
6. Pre-delivery gate
7. Change log

## 1. Standing requirements from the user

These came from the user in his own words or as corrections. They are not optional.

1. Cover every source type below without being reminded, including YouTube, Medium, Substack, podcasts, speaking engagements and review sites.
2. Read postings and articles. Do not infer from titles or keyword counts.
3. Every fact, posting, article and person carries its URL in the same row.
4. Deliver the summary and the detailed evidence as two separate Word documents. People go in one Excel workbook.
5. In the Summary, put the plain language narrative first, then the caveats on interpretation, then the graded tables, then the tracker.
6. Include a research coverage tracker in every Summary with status, priority, what it needs, what was done and what remains, sorted so the most valuable open work is first.
7. Anything told to the user in chat about the account must also be in a document.
8. Word format for documents, proper margins, no em dashes, no hyphens or dashes used as punctuation, no mid sentence colon splices. Every claim must appear in the source cited for it. Label interpretation as interpretation.
9. Check the documents before sharing them. The user should never be the one who finds a missing section, a broken table or a missing link.
10. Do not pull data that is already pulled. Continue from the next page of a source instead of reloading it.
11. Keep one deduplicated people list per account. Do not carry overlapping people tables in the documents.
12. Exclude CISO, security, IT compliance and IT audit people from people lists. Security incidents stay in the brief as account signals.
13. For Salesforce partner clients, never frame anything as a customer leaving Salesforce or as a Salesforce failure.
14. Never send outreach, never delete or overwrite the user's records, and ask before spending credits the user has not approved.
15. Client facing deliverables never name the data or enrichment tools used, or their credit costs. Say 6th agents if attribution is needed.
16. Plain English everywhere. No phrases an executive has to decode. Internal program names are explained as internal names, and every abbreviation is written out on first use.
17. Name the incumbent partner and the key executives when known. Do not hide behind "a major services firm".
18. When a file is re-shared, open with one line on what changed. When a claim is questioned, show the exact source sentence.
19. Do not overstate patterns. A detection feed date range, a keyword count or an alumni count is a lead, not a finding. Write what the data shows and grade it.
20. Every count states its base and what it includes. Reconcile counts across files before delivery.
21. Do not invent content for a requested section. If there is no sourced material for something, say so and leave it out.
22. Decks are not built unless the user asks for one in the current conversation. When asked, the executive deck carries speaker notes on every content slide split into confirmed, what we are guessing and what we have a hunch on.
23. No internal call recordings anywhere in an account's files: not as sources, not as links, not as a "client call recording" row. Client emails may be cited by date and subject with no thread link once the file leaves the user. Notes taken from a call go in the workspace notes as "call notes, date" without a link.
23a. Anything important learned from a call, an email thread, an internal conversation or a similar non public source that names client or account team people, or that should not leave the user, goes in a separate file `{Account}_Rajesh_Only.docx` marked "Rajesh Only" on the cover and in the header. That file is never placed in the client share, never zipped into the workspace, and is listed on its own line in the delivery note. The client facing files carry the same finding only in its neutral form ("Client", "call notes, date") or not at all.
24. No client or CitiusTech staff names in "who to ask", next step or open item lines. Write "Client". Getting answers from the account team is the user's job; the files stay on the research.
25. One account per file set. Another account's name never appears in an account's Summary, Evidence, workbooks, workspace notes or the chat about it, including email thread subjects used as source labels.
26. The workspace zip is a client shareable object. It gets the same tool name scan as the deliverables: raw data files carry neutral names (techstack_797.json, not a vendor prefix), pull notes say "6th agents", and no credit counts appear in notes.

## 2. Scope defaults

Use these unless the user says otherwise for the account.

- Platform lanes: Salesforce, ServiceNow, SAP, Tableau and analytics, Veeva, Data and AI that is not tied to a platform. MuleSoft counts under Salesforce and is searched on every account (board keyword, job keyword, detections, people titles). ServiceMax is out of scope on every account: do not search for it, pull people for it, or build findings on it; a record that mentions it in passing stays as pulled. Veeva covers Commercial (Veeva CRM, Vault CRM, Vault PromoMats, MedComms, OpenData, Crossix) and R&D and Quality (Vault Clinical, RIM, Quality, Safety) and is a primary lane for life sciences accounts; for other industries record it as None once and move on.
- Seniority rule for people: Director and above, Head, and Senior Manager. People below that level are kept only when they confirm a fact about the stack.
- Geography: global.
- News window: 18 months. Signals older than their shelf life go to a context and history table.
- Pursuing client: ask once. If unknown, mark the relationship rows as waiting on the account owner.

## 3. Run steps

1. Read this checklist and `lessons.md`. Read `tools.md` before the first tool call.
2. Confirm account name and domain. Resolve the verified careers site URL by visiting it.
3. Pass 1, summary homework: run the five Pass 1 rows.
4. Company baseline: latest three earnings releases or equivalent, the newsroom list, leadership page. Re-open each release to verify every figure you will quote.
5. News: company releases, then a structured news feed (the job and news feed) for third party items, then targeted searches for anything the feed hints at. Verify the important items against their source article.
6. Jobs: search the live job board once per lane keyword. Then pull every posting first seen in the last 12 months, open and closed, from the job feed. Read the platform sentence in every posting that names a platform, and read in full every role where the platform is the job. Classify each as Build role, Admin and owner role, Working tool, or User role. Record counts as read versus matched, per platform, open versus closed.
7. Tech stack: the job and news feed detections, cross checked against postings. Note first seen and last seen dates.
8. People: title search with the two title lists in `phase1-pull.md` step 6 (titles and domain only, no seniority or department filter), then technology association search only for gaps, merged into the grid before profiles, then people named in research with profile URLs found by search. Merge, dedupe, apply exclusions, assign level, lane and priority.
8a. Profiles: pull full public profiles for every senior person. Compute prior employers, join dates, role start dates, tenure, certifications and locations in code. Scan the role descriptions people wrote about their jobs at the account for program names, platforms, tools and partners. Report alumni counts by firm with the caveat that they cover any point in a career. Write the findings as a separate section next to the people list.
8b. Executive voice: read the last two earnings call transcripts and the last results release. Record focus, opportunities and challenges in the executives' words, and the industry, market and company specific challenges they name. Verify every figure in the release.
9. Person name searches: speaking past and upcoming, podcasts, YouTube, Medium, Substack, vendor customer stories. Run these after the people list exists, and search by person name, not company name.
10. Review sites and vendor communities. Expect None for most accounts and say so.
11. Distill: grade every line, write hypotheses with a confirming question each, write first call questions.
12. Build the standing files from `output-spec.md`: summary, evidence, people workbook, jobs workbook, tech stack and teams workbook, workspace zip. No deck unless asked. The tech stack and teams workbook opens with the Platform Ranking sheet: every platform named in the 12 month postings, ranked A, B or C, built with `assets/platform_ranking.py`.
13. Run the pre-delivery gate below. Fix, rebuild, rerun.
14. Deliver once. State what was found, what was corrected during checking, what is open, and credits used.

## 4. Coverage rows

Each row appears in the Summary tracker with a status, and with proof for anything marked Done or None: the queries, the tool and the result count.

### Pass 1: summary homework

| Row | Default priority | Usual tool | Minimum to call it Done |
|---|---|---|---|
| Prior customer status, past interactions, prior pursuits | High | Client CRM, Grain, Gmail | Searched by company name and domain in each system |
| Connections, alumni overlaps, former colleagues | High | LinkedIn, the association search tool | Checked for the top ten people |
| Platform vendor account team | High | Network, partner portals | Named or marked unknown |
| LinkedIn profiles and posts of key contacts | High | the title search tool | Posts pulled for the priority A shortlist |
| Basic fit check | High | Any | Industry, size and lanes confirmed |

### Pass 2: detailed homework

| Row | Default priority | Usual tool | Minimum to call it Done |
|---|---|---|---|
| Company news and press releases | High | Newsroom fetch, the job and news feed news, page fetch | Newsroom list read, feed pulled, key items verified at source |
| Earnings releases and filings | High | page fetch of the release or SEC filing | Last three periods read, figures verified |
| Leadership changes, last six months | High | the job and news feed news categories hires and leaves | Feed checked, technology and commercial leaders noted |
| Job board search by lane | High | board scrape of the live board | One search per lane keyword, counts recorded |
| Job postings read | High | Page fetch, job feed descriptions | Every match read for primary lanes, each classified |
| Job postings, last 12 months, open and closed | High | Job feed, three pages | Every platform sentence read, every build role read in full, counts by platform and status, jobs workbook built |
| Technology detections | High | the job and news feed | Pulled, lane relevant rows kept with dates |
| People by platform | High | Title search, then association search for gaps | Senior list complete, merged and deduplicated |
| Full profiles of senior people | High | Profile pull, one credit per person | Every senior person pulled, findings section written, alumni and tenure counts computed |
| Posts and comments of priority people | Medium | Posts and comments pull | Priority A people pulled, activity in last 90 days counted; skip the rest if the group is quiet |
| Executive voice from earnings calls | High | Transcript fetch | Last two calls read, focus, opportunity and challenge captured with the speaker |
| Financial baseline verified | High | Results release, SEC filing | Sales by segment, guidance, charges, efficiency programs, tariffs, deals, each figure re-read in the release |
| Vendor customer stories | High | page fetch by vendor plus company | Each lane vendor searched |
| Verification of items graded Lead | High | Any | Each Lead confirmed, regraded or left with a reason |
| Customers and partners matched to client references | High | Client proof library | Needs the client name |
| Executive speaking, upcoming | Medium | page fetch by person name | Top ten people searched |
| Executive speaking, past | Medium | page fetch by person name | Top ten people searched |
| Podcasts | Medium | page fetch by person name | Top ten people searched |
| YouTube | Medium | page fetch, board scrape for transcripts | Company and person searches, relevant transcripts read |
| Medium and Substack | Medium | board scrape or page fetch with domain filter | Two differently worded searches |
| Review sites | Medium | page fetch per site | TrustRadius, PeerSpot, G2, Gartner Peer Insights |
| Vendor communities and marketplaces | Medium | page fetch | Trailblazer, ServiceNow community, SAP community, AppExchange |
| Regulatory and quality events | Optional | FDA databases, news feed | Checked for medtech and pharma accounts |
| Financial position against peers | Optional | Filings | Two or three peers compared |
| Competitor moves and peer KPIs | Optional | Filings, press | Noted where useful as a give |
| Major holders and activist activity | Optional | Filings | Top tier accounts only |
| Events sponsored or exhibited at | Optional | Event sites | Sponsor lists checked |
| Employee content, GitHub, engineering blogs | Optional | board scrape developer search | Searched by company |
| Research papers | Optional | board scrape research tools | Medtech and life sciences accounts |

Add a row whenever the user names a new source. Never remove a row without the user saying so.

## 5. People rules

- Build with the title search on the company domain, sending titles and domain only. Never send a seniority or department filter with titles: the filters intersect and cut the match count by 80 to 90 percent (tested Oct 6 2026 on availity.com: 63 matches with titles alone, 5 with seniority added). Put the level into the title words instead. Run two sources with the lists in `phase1-pull.md` step 6: leadership and platform titles, then manager titles; keep Senior Manager rows from the manager set by filtering the title text. The title search tool has no Senior Manager level. A source that matches under 15 rows at a company above 500 employees is a wrong query, not a result: log the parameters, remove any level or department filter, rerun once.
- Load the next page with a pull on the existing source. Never add the same source twice.
- Use technology association searches only to find executives whose titles name no platform, or when the title search tool leaves an obvious gap. Restrict the association search tool searches to IT, engineering and product functions, since an unrestricted technology search returns business users.
- Merge by LinkedIn profile first and by name second. Note second profiles instead of keeping two rows.
- Exclude security, IT compliance and IT audit titles. List who was excluded on the workbook Summary sheet.
- Assign level from the title, lane from title keywords, and priority A, B or C. Say in the workbook that these are judgments from the title and can be corrected there.
- Titles from these tools are self reported. Grade them Lead until a profile is checked.
- The documents carry only a key people table, priority A and senior, and point to the workbook for the rest.

## 6. Pre-delivery gate

Do not present any file until all of these pass.

1. Convert each .docx to PDF and run `scripts/rowcheck.py`. Zero missing rows.
2. Run `scripts/doc_checks.py` with the source ID list. Zero orphan citations, zero banned dashes, zero stale phrases, zero excluded names.
3. Recalculate the workbook and confirm zero formula errors. Confirm the totals match the row counts.
4. Recompute the tracker tally from the rows. Do not type it by hand.
5. Look at the rendered pages of every new or changed table.
6. Reread the narrative against the tables. Every statement in the narrative needs a source ID in brackets and a matching graded row.
7. Check each number quoted in the chat reply against the file.
8. Confirm that everything said in chat this turn is in a file.
9. Scan every file for data tool names and the word credits. Zero hits. Run the scan on the unzipped package (word/*.xml, word/_rels/*.rels, xl/*.xml, ppt/*.xml, docProps), not only on the rendered text, so a name hidden in a relationship id, a comment, a property or a formula fails the same way a name in a sentence does. Run `scripts/third_party_scan.py <file>...` which does this.
9a. Third party source check. Before sharing, list every external domain the files link to or name (from Part I, the Source links section, every hyperlink relationship, and every URL cell in the workbooks). Sort them into three groups and write the list in the delivery note: (1) the account's own sites and public press, fine to keep; (2) data tools and enrichment vendors, which must not appear at all; (3) aggregators, profilers and other third party data sites (stack profilers, case study indexes, review sites, list brokers). Group 3 stays only if the claim it supports is graded Lead and the file says an aggregator said it; otherwise replace it with a primary source or drop the claim. `scripts/third_party_scan.py` prints the domain list; the sorting is a read, not a grep. Any group 2 hit or an unsorted group 3 domain blocks delivery.
9b. Platform Ranking sheet: the base on the sheet equals the platform naming postings in the jobs workbook; every A rank was checked by reading (the script's `rank_A_not_checked_by_reading` list is empty); no Databricks, Siebel or ServiceMax row; zero formula errors after recalculation.
10. For the deck: validate, render every slide, look at each one for overflow, and check that every content slide has the three part notes.
11. Reconcile every count quoted in the reply against the files, with its base stated.

## 7. Change log

- 2026-10-07. Version 1.11. Three corrections from the lower cost model's own review of its Availity run. People rule in section 5 rewritten to match phase 1 and the tools playbook (titles and domain only, never seniority or department; the 1.10 change had left this section contradicting the runbook). Gap check people are merged into the grid before profiles and posts run (new step 6a), so profile and post counts follow the merged list, not the title search alone. Spend priority inside the cap written down: people completeness first, then feeds and stack, then newsroom and media; media stubs are never bought at the expense of profiles. Teams call set at 6 attributes (the four in use plus technology list and location list, which carry real content; job posts and related people are dropped because they return empty) and the Tech Stack and Teams workbook gains Team technologies and Team locations columns so phase 2 uses them.
- 2026-10-06. Version 1.10. Phase 1 runbook rewritten for a rule following model after the first lower cost model run (Availity, Oct 6 2026) returned 9 people against 97 from the same two title searches, missed the Salesforce partnership release, searched one careers board of two, left another program account's name in the news file and named tools in the pull log. Every step now has the call with parameters, the expected result range and the retry rule; a completeness gate (step 12) runs before packaging; exact parameters of every paid call are logged. People searches send titles and domain only, never seniority or department (tested: 63 versus 5 matches). Lane vendor partnership search, every careers board, connections and subsidiary pulls made mandatory. Teams call fixed at 4 attributes. Databricks removed from the step 4 live board keyword list, where it had survived the 1.8 change.
- 2026-10-01. Version 1.9. The Tech Stack and Teams workbook gains a first sheet, Platform Ranking: every platform named in the account's 12 month job postings, ranked A, B or C by share of platform naming postings (A at 15 percent and 5 postings, B at 5 percent and 3 postings, C at 2 postings, single mentions listed unranked). Covers platforms outside the lanes. A ranks are confirmed by reading at least three postings. New asset `assets/platform_ranking.py`, run step 12 and gate check 9b updated.
- 2026-09-29. Version 1.8. ServiceMax removed from scope on every account at the user's request (no searches, no people pulls, no findings built on it). MuleSoft added to the Salesforce lane and to every keyword list: live board, job keyword counts, technology filters and people title variants.
- 2026-09-27. Version 1.7. Split mode is now the default: phase 1 pulls on the lower cost model, phase 2 on the stronger model. Phase 2 gains a mandatory sense check (step 2a in `phase2-build.md`) before any building: entity boundaries, adjacent organizations, hidden titles and program names, contradictions, missed people, and a ranked list of targeted pulls with credits and the decision each serves, presented to the user for a yes. Phase 1 gains a capture rule so entity labels, source domains and current employers travel with every record. Reason: the judgment a single model run applies during the pulls (a New York plan mistaken for the account, hospital roles tagged to the plan, parent IT holding the data platform) must not be lost when the pulls move to a model that follows rules only.
- 2026-09-26. Version 1.6. Split mode added as an option: phase 1 pulls on one model (`phase1-pull.md`), phase 2 analysis and deliverables on another (`phase2-build.md`). Same checklist for both. Phase 1 over-collects, never interprets, keeps every people row, pulls posts for all Director and above titles by rule, and ships a pull log that becomes the tracker's proof column. Phase 2 works from raw files only and sends back at most one second pull list. Single model run stays the default.

- 2026-09-25. Version 1.5. Rajesh Only file added (requirement 23a, output spec) for important findings from calls and internal sources that name client people or must not leave the user. Deck removed from the standing deliverables everywhere in the skill text; it is built only on an explicit request. Veeva added as a platform lane (Commercial and Vault families), with fingerprint questions, job keyword patterns, people title variants and a people workbook lane. Life sciences accounts treat it as primary; other industries record None.

- 2026-09-23. Version 1.4. One credit cap per run agreed at the start; balances checked first; paid readers (Firecrawl) first for job boards and postings; free readers only for search and articles. Read every primary lane match before judging relevance.

- 2026-09-22. Version 1.3. Paid results always inline, never as hosted download files; exact cost computed before every paid call and confirmed when above the agreed estimate. Healthcare rows removed entirely for accounts outside that industry. Public company runs cover four quarterly calls, filings, investor day and conference remarks. Large pulls go straight to files.

- 2026-09-22. Version 1.2. Lessons log rewritten as general rules with account specifics removed. Abbreviations written out on first use. People workbook gains a former systems integrator employers column.

- 2026-09-22. Version 1.1. Added standing requirements 15 to 22: no tool names in client files, plain English, name incumbents and executives, say what changed on re-share, do not overstate detection patterns, state the base of every count, never invent content for a requested section, three part speaker notes. Added run steps for 12 month closed postings, full profile analysis and executive voice from calls. Added coverage rows for closed postings, profiles, posts, executive voice and financial baseline. Added gate checks 9 to 11. Deliverables now five files including the jobs workbook and the executive deck. Tool names removed from this file.

- 2026-09-20. Version 1.0 created. Seniority rule set to Director and above, Head, and Senior Manager. Security, compliance and audit people excluded from people lists. The title search tool made the default people tool, The association search tool reserved for gaps and for account list building. The job and news feed added for news, technology detections and job descriptions. Page fetch added as the default open web search and page reader. Board scrape added for the live job board and YouTube transcripts. Tableau and analytics added as a lane.
- Sep 24 2026: Sumble file_name ban widened to every tool on the server after a repeat on an earlier account. Lessons entry added.
- Sep 24 2026: Two standing deliverables added. (a) <Account>_Tech_Stack_and_Teams.xlsx: the Sumble confirmed used stack and platform teams pull, internal only, strip vendor links before client use. (b) <Account>_workspace.zip with RESTORE.md, deliverables, build scripts, raw data and notes, shipped with every delivery and refreshed on every re-delivery. Executive Brief deck is built only when the user asks for it.
- Sep 24 2026: Drive rule, all accounts. Search Drive only inside folders the user or client has named for the run. If a client owned folder or file surfaces from a wider search, stop, list what surfaced by title and owner, and do not read it until the user clears it. Signed contracts, SOWs and addenda are quoted in deliverables only at the level a seller needs (what was delivered, on which platform, roughly when, and that reference use needs clearance): never the counterparties beyond the client, budgets, signatories, or document numbers. The chat gets the same restraint as the file. An earlier account's S31 was reduced to this level on Sep 24.
- Sep 23 2026 (Highmark v2): Standing requirements 23 to 26 added after the user found call recording sources, client staff names and another account's thread subject in delivered files. Gate 9 extended to the workspace zip. Post dates from a posts pull are derived from the post identifiers (the provider returns none). A container reset loses the workspace; the workspace zip is the restore point, so ship it every time.
- Sep 24 2026: Gate 9 now scans unzipped package XML. Gate 9a added: third party source domain check before every share, after two tool names were found inside file internals on an earlier account.
