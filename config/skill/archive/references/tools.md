# Tools playbook

Which tool to use, what works, what it costs, and the quirks found so far. Tool availability changes, so check what is connected at the start of a run. If a tool below is missing, fall back as noted and record the row as Blocked or Partial.

## Routing at a glance

| Need | First choice | Fallback | Notes |
|---|---|---|---|
| Open web search with long excerpts | Parallel web_search | Built in web search | Free. State an objective plus three or four keyword queries. Reuse one session id per run |
| Read a known page (news, filings, articles) | Parallel web_fetch | Built in web_fetch | Free. Give an objective so the excerpt is targeted. One URL per call; it rate limits after a few calls in a row |
| Job boards, job postings, pages that need a browser, YouTube | Firecrawl scrape | Parallel web_fetch | One credit per page, full text. First choice by the user's standing preference: quality over free |
| Structured news events, technology detections, job descriptions | PredictLeads | Parallel search | Credits per call. Pull once per run and save locally |
| People by title, company wide | BitScale Find People | Sumble | No visible credit cost on the Booster plan as of Sep 2026 |
| People by technology association, executives | Sumble people search | BitScale with leadership titles | One credit per person plus one per attribute |
| Account list building by technology | Sumble organizations search | BitScale Find Companies | Sumble is stronger here |
| LinkedIn posts and engagement | BitScale enrichment column | None | Credit spending, estimate first |
| Client history | Grain, Gmail, Drive, client CRM | None | Needs the client name |

## Budget cap and paid first rule

- The user prefers paid, complete work over free, partial work, and will top up credits when needed.
- At the start of every run, propose one credit cap for the whole run across all tools, with a short breakdown, and get a yes. Within the cap, run without stopping to ask and report every cost at the end.
- Anything that would exceed the cap, or any single call above the agreed estimate, comes back to the user first.
- Check balances (free) before starting. If a tool's balance is below what the run needs, tell the user to top it up before starting rather than falling back to a weaker free source.
- Free sources stay first only where they work well: web search and reading news, filings and articles.

## Parallel Search

- Write the objective as a sentence describing what you need and any source guidance, then add short keyword queries.
- Search by person name for speaking, podcasts and interviews. Company name plus the word conference returns noise.
- For a lead that came from a snippet, phrase queries the way a journalist would: company, city, plain nouns. A snippet's own wording often fails; a plain query with company, city and a plain noun such as expansion usually works.
- LinkedIn snippets mix a person's own posts with posts they reacted to. Treat them as leads.

## Firecrawl

- Live Workday job board: scrape `{verified base url}?q={keyword}` with formats links, waitFor 4000, maxAge 0. The links list gives every matched posting URL. Get the base URL by visiting the careers site, never by guessing the tenant.
- Plain scrape costs one credit. JSON extraction costs five. Prefer plain scrapes and parse locally.
- A search with scrapeOptions scrapes every result and can cost twenty or more credits. Search first, then scrape selectively.
- YouTube: scraping a watch URL returns the transcript.
- Job board searches and individual postings go to Firecrawl first, within the run's budget cap. Free readers returned partial excerpts, missed platform sentences and rate limited mid run, leaving rows half done. Use Parallel for postings only if Firecrawl fails.
- Never use the query or JSON formats on job pages; they cost five credits and returned nothing useful. Use a plain scrape and read the text.
- Monitoring (scheduled scrapes with change detection) exists for repeat watching of a job board. Use only with the user's approval.

## PredictLeads

- Call by domain. Results are large, so they land in a file under the tool results folder. Parse with Python, save a compact JSON next to the account files, and work from that.
- `company_news_events` with found_at_from set to the start of the window. Expect about a third noise: charity partnerships, law firm blogs, misattributed events. Event dates inside summaries are sometimes wrong even when the article is right. Verify anything important at the source article. Mark unopened items Feed only.
- `company_technology_detections` with limit 1000. Keep rows last seen in the past two years. Score is confidence that the technology was named, not depth of use.
- `company_job_openings` with active_only true and the technology, data and management categories. Descriptions are included, so keyword and context searches can run locally for free.
- `company_connections` is mostly noise for large companies. Skim for consultancies and software vendors that name the account as a customer.
- The response meta often names the parent company domain. Note it.

## Sumble

- Resolve the organization with name plus domain in match mode. The web interface can fail on a bare name where the domain works.
- People search is scoped to an organization id. Query by technology slug and job level, and restrict job_function to Information Technology, Engineering and R&D, Product and Design, Revenue Operations to avoid business users.
- Request only job_title, job_level, linkedin_url and location. Emails cost ten credits and phones eighty, so leave them until the user has chosen who to contact.
- Always share the sumble_url for a person or search alongside the LinkedIn link.
- Sumble misclassifies some process owners as C level. Check levels against titles.
- Offer a small sample before any call above a few hundred credits.
- Never set file_name on ANY Sumble call: people, organizations, tech stack, teams, signals, SQL, all of them. The file is hosted on the vendor's site, which neither the page reader nor the sandbox can open, so the credits are lost and the call has to be repeated inline at full cost. Return results inline and save them locally yourself. Repeated on GSK, Sep 24 2026, at 72 credits: the rule applies to every tool on the server, not only the two it was first written for.
- Cost is people returned times one plus each paid attribute. Compute it before the call; if it is above the estimate already given to the user, state it and wait for a yes.

## BitScale

BitScale enforces a consent flow. Follow it, since it matches the user's own rules.

1. Before any write, call `ask_human_v1` to load the policy. If titles, geography or row count have not been stated by the user, ask with the client's option buttons in one batch, with a recommendation.
2. Call `plan_bitscale_workflow_v1`, then state the plan in plain language and wait for an explicit yes.
3. `get_source_type_details_v1` for the schema, `get_workspace_v1` for the credit balance before and after.
4. `create_grid_v1`, then `add_grid_source_v1` with type people. **Send `person_titles` and `q_organization_domains` only. Never send `person_seniorities` or `person_department_or_subdepartments` with titles.** The filters intersect, and the intersection destroys coverage: tested Oct 6 2026 on availity.com, the same title list returned 63 matches alone and 5 matches with seniority c_suite, vp, head, director added. Put the level into the title words instead (Chief, VP, Head, Director, Manager), as the two source lists in `phase1-pull.md` step 6 do.
5. Poll `get_grid_status_v1`. matched_upstream is the total match count and is the number to report. People sources load in blocks of 100.
6. To get more rows, call `pull_latest_source_data_v1` on the same source. It continues from the next page. Confirm the row count rose by the expected amount.
7. Read rows with `get_rows_v1` and column_keys for name, title, headline, LinkedIn URL, company and location. Keep the employer label verbatim.
8. Never delete rows, sources or grids without the user's confirmation in chat.
9. Adding and running an enrichment column such as LinkedIn posts spends credits. Estimate on five to ten rows with confirm false, show the estimate, and wait for a yes.
10. Log the full `person_titles` list and every other filter sent, with the matched_upstream count, in the pull log. A count under 15 at a company above 500 employees means the query is wrong; check for a seniority or department filter and for the domain, rerun once, log both counts.

Known good title lists are in `phase1-pull.md` step 6 (leadership and platform titles; manager level; life sciences and SAP additions). The older platform word list (Salesforce, CRM, Service Cloud, ITSM, Enterprise Applications and so on, without a level word) returned 27 matches on the same test and is kept only as a supplement for accounts where the leadership list returns under 30. The variant Field Service pulls in many individual engineers; use it only on service heavy accounts and expect to filter.

## Closed postings over 12 months

`company_job_openings` with `active_only` false and `first_seen_at_from` set to a year ago returns open and closed postings, 1,000 per page; page three times for a year at a large company. `status` is `closed` or null, and null means open. Descriptions are included, so read platform sentences locally. Count people and postings separately from records, and note reposted requisitions.

## Full profiles and activity

The profile pull returns every role with company, title, dates and the person's own description, plus certifications, skills, location and followed companies. Run it for every senior person; it is the richest evidence in the run. Compute alumni, join dates, role starts, tenure and certification counts in code, merging firm name variants and counting each firm once per person. Posts and comments return the provider's recent page with no date filter; senior people at quiet companies rarely post, so pull posts for priority A only and decide from there. Reactions given by a person are not available.

## Earnings calls and releases

Results releases are on the investor site and as SEC exhibits. Transcripts are on Motley Fool, Seeking Alpha and Investing.com; fetch one in full. Read the last two calls. Record what the executives said as focus, opportunity and challenge with the speaker, verify every figure against the release, and note where a number is derived rather than printed.

## Tools considered and not in use

- Perigon: good structured news, priced from about 250 dollars a month. PredictLeads covers the need for now.
- Microsoft 365 connector: rejects personal Microsoft accounts, so OneDrive is not reachable from claude.ai.
- Obsidian vault: reachable from the desktop app only.
- Perplexity: no connector in the directory.
