# Lessons log

Each entry records what went wrong or what the user asked for, and the rule it produced. Newest first. Add an entry in the same conversation as the correction.

## 2026-10-07, the lower cost model reviewed its own run

- **The user had the lower cost model read the comparison and diagnose itself.** It found that checklist section 5 still said "run once with seniorities", contradicting the 1.10 runbook and the tools playbook; it had followed the checklist. Rule: a rule that lives in more than one reference file is changed in every file in the same edit, and the pre-share step for a skill version is a grep of the old wording across all references.
- **It also found that its 25 gap check people never reached the profile and posts steps** because they sat in their own file. Rule: gap check runs before profiles, merges into the grid, and the log carries the three counts (title rows, gap rows, merged rows).
- **On team records it had pulled more than the earlier run** (full technology list, locations, first activity), and the workbook had no columns for it. Rule: when a phase 1 pull returns fields phase 2 cannot place, the fix is a column in the output spec or a dropped attribute, decided in the same conversation; never a silent discard.

## 2026-10-06, first lower cost model run

- **The user ran phase 1 on a rule following model and the people pull came back with 9 rows where the earlier run had 97.** The runbook said "two title searches with the variants in tools.md", which both runs could truthfully claim to have done; neither log recorded the parameters sent, so the cause could not be read from the files. A test on the same account showed the cause: a title list sent with a seniority filter returns 5 matches, the same list alone returns 63. Rule: phase 1 steps state the call, the expected range and the retry; every paid call's parameters go in the log verbatim; title searches never carry seniority or department filters.
- **The same run missed things a judging model infers:** the second careers board linked from the careers page, the Salesforce partnership release (found by the earlier run through a vendor name search), the connections and subsidiary pulls, the other program account in the news feed, neutral tool labels in the log. Rule: each of these is now an explicit line in the runbook and a row in the completeness gate. Nothing in phase 1 depends on the model noticing.
- **The runbook still listed Databricks as a live board keyword** after the Sep 29 scope change removed it elsewhere. The lower cost model searched it because the file said to. Rule: a scope change is applied by grep across every reference file, not by memory of where the term appears.

## 2026-09-29, lane keywords

- **User said ServiceMax is never needed and asked to add MuleSoft.** Rule: ServiceMax is dropped from every search, people pull and finding on every account; MuleSoft is searched on every account as part of the Salesforce lane. Technology filters for the stack and people tool follow the same list (drop servicemax, keep mulesoft).

## 2026-09-27, the sense check

- **User asked that the judgment applied during single model pulls not be lost when pulls move to a lower cost model.** On the Johns Hopkins run that judgment found three people and about twenty postings that belonged to a New York plan with the same product name, hospital roles tagged to the health plan, and the parent's Health IT holding the Databricks, Fabric and MuleSoft platforms. Rule: phase 2 starts with a written sense check (entity boundaries, adjacent organizations, hidden titles, contradictions, missed people, what would change the brief) and recommends targeted pulls with credits and the decision each serves; the user says yes; judgment heavy pulls run in phase 2, bulk pulls go back to phase 1. An empty recommendation with reasons is fine; never pad it.
- **Split mode made the default** after the usage limit and price difference between the two models was confirmed: the pull phase is most of the tokens and least of the judgment.

## 2026-09-26, split mode

- **User asked whether data pulls could run on a lower cost model and analysis on the stronger one.** Rule: split at step 4; the pulling model collects into files with a full pull log and makes no judgment calls, so anything that needed judgment in a single run (priority lists for posts pulls, which postings to read) becomes a mechanical rule or moves to phase 2. Phase 2 never inherits phase 1's interpretation. Keep one checklist for both phases so corrections apply to both.
- **Tool lessons from AmeriHealth Caritas.** Platform teams pulls are the largest single spend; cap them at about 25 teams. The web search reader rate limits after a few calls; switch to the paid scraper for event agendas and speaker pages. A careers site can move tenants mid year; older postings keep the old tenant links.

## 2026-09-25, scope change

- **User confirmed the tool name and client staff name checks stay, and added a channel for the exceptions.** Rule: when something super important comes from a call or similar and it names client people or must not leave the user, do not drop it and do not put it in the client files; write a separate `{Account}_Rajesh_Only.docx`, keep it out of the share and the zip, and give it its own line in the delivery note.
- **The skill description still listed the executive deck as a standard deliverable after the user had said no deck unless asked.** Rule: the deck is off by default in every file of this skill; never offer it, build it or count it among the deliverables unless the user asks for one.
- **User asked to add Veeva to the platform lanes.** Rule: Veeva is the sixth lane, alongside Salesforce, SAP, ServiceNow, Tableau and Data and AI. Search it on every life sciences account across jobs, tech detections, people titles and vendor customer stories; look specifically for Veeva CRM to Vault CRM migration timing, since Veeva CRM runs on the Salesforce platform and the migration changes the Salesforce picture at the account.

## 2026-09-23, rules from the Highmark version 2 session

- **Delivered files cited two internal call recordings and linked the user's own email threads.** The user asked for every reference to internal recordings to be dropped. Rule: recordings are never a source, a link or a row type in any account file or workspace note; what was learned on a call is written as a fact with "call notes, date" and no link.
- **"Who to ask" and next step lines named client and CitiusTech staff, and the chat drafted questions for them.** The user: "you don't work for them." Rule: write "Client" in the files, never a person's name, and never draft the ask unless requested. Research stays on the account; relationship questions belong to the user.
- **A source label carried another account's name because the scope email lived on that account's thread.** Rule: one account per file set; relabel sources by subject and date, never by thread title, and keep other accounts out of the chat about an account.
- **The workspace zip carried vendor prefixed file names, tool result file names and a credit line while the deliverables were clean.** Rule: the zip ships with the deliverables and gets the same scan; name raw pulls neutrally from the start.
- **Container reset between conversations lost the whole workspace; the user re uploaded a zip that had diverged from the last build (it carried a newer Job Postings workbook).** Rule: the workspace zip is the restore point; diff the uploaded zip against the working copy before touching anything, merge, then continue.
- **Tool lessons.** The posts pull returns no dates; derive them from the LinkedIn activity identifier (id shifted right 22 bits is epoch milliseconds). Reading 39 profile cells in one call lands in a stored file that can be parsed locally; paging in twenties floods the context. AppExchange consulting listings are readable without login and can reveal that the account itself sells the platform's services.

## 2026-09-23, rules from the Workday run

- **Free readers left the job board work half done.** Parallel page fetch rate limited after a few calls, returned excerpts that skipped the platform sentence, and forced several rounds of partial work and follow up. The user prefers paying for complete work. Rule: use Firecrawl first for job board searches and postings; propose one credit cap for the whole run at the start, run within it without stopping, and ask the user to top up a low balance rather than falling back to free sources.
- **Judged postings on titles alone, then reversed.** Rule: read every match in the primary lanes before judging relevance, as the checklist says. Never recommend skipping postings on titles.

## 2026-09-22, rules from the second run

- **Paid for a people search written to a download file that could not be opened.** The vendor hosts the file on its own site, the page reader only opens links from earlier search results, and the code sandbox cannot reach that site. The same people then had to be bought again inline, and the step cost several times the estimate given to the user. Rule: always return paid results inline, never as a hosted download file. Before any paid call, compute its exact cost (people times one plus paid attributes), and if it exceeds the estimate already agreed with the user, state the number and wait for a yes. Test a new tool option on one to five rows first.
- **Asked for a scope without healthcare and life sciences.** Rule: when the account is not in that industry, remove the regulatory and research paper rows entirely, not marked skipped, and keep healthcare framing out of every file.
- **Asked for all conference call transcripts for a public company.** Rule: for public companies, read the last four quarterly calls, the results releases, the latest 10-K, 10-Q, proxy and 8-Ks, investor day material, and investor conference remarks, and track each as a coverage row.
- **Tool lessons.** The free page fetch rate limits after a handful of calls in a row, so fetch one page at a time and pull job descriptions from the job feed instead of the live board. A single reply has a working budget, so send large pulls straight to files, never paste hundreds of rows back into the conversation, and build the five deliverables in separate steps.

## 2026-09-22, rules from the executive deck and profile work

- **Detection date ranges are not decisions.** A technology name first seen one year and last seen another means it appeared in public material for that period. Never write that it was evaluated, adopted or dropped. Check whether the name could be a loose synonym for another product.
- **A few profiles are not a company direction.** When individual role descriptions mention a tool, say how many people, in what roles, and that they are pilots or personal scope. Grade any trend reading as a hunch.
- **Count people, not records.** One person can have two job entries at the same company. Deduplicate before counting and be ready to list the names.
- **Describe what a company disclosed, and say what it did not.** Do not give an unnamed initiative a name, and do not fill in components the company has not itemized.
- **Every count names its base.** When two lists overlap, say which list a number is measured against and reconcile before replying.
- **Plain English.** If a phrase needs decoding, rewrite it. Explain internal program names as internal.
- **Name incumbents and executives** when the evidence supports it, and grade the incumbent claim.
- **Re-shared files open with what changed.**
- **Client facing files never name data tools or credit costs.**
- **Keep lessons general.** Write the rule, not the account story. Account specifics belong in the account files, not in this log.
- **Tool lessons.** The job feed keeps closed postings; pull a year with a first seen filter and paginate. Profile pulls return every role with dates and the person's own description, which is the richest evidence in a run. Senior people at many companies rarely post; pull activity for priority people first and decide. Prior employer counts must merge name variants, count each firm once per person, and discount a former parent company. Earnings call transcripts are published within a week; read the last two.

## 2026-09-20, rules from the first run

- **Asked for YouTube, Medium, Substack and speaking engagements, and they were skipped.** The instruction lived only in the conversation. Rule: every source type is a row in the checklist, and every row ends the run with a visible status and proof.
- **A strong narrative was given in chat and left out of the document.** Rule: anything said in chat about the account must be in a file. Narrative comes first in the Summary.
- **Priorities were explained in chat as three piles and never added to the tracker.** Rule: the tracker carries Priority and Needs columns and is sorted by them.
- **Only 1 of 21 matched postings had been read in full, while the summary implied more.** Reading the rest found a false positive and a generic mention, and also the best finding of the run, a Salesforce developer role hidden behind a DevSecOps title. Rule: read every match for primary lanes and classify each one.
- **Job links were missing from most rows.** Rule: no fact without its URL in the same row, plus a full URL appendix in the evidence file.
- **A table rendered with 1 of 12 rows because it crossed a page break.** Rule: run rowcheck on every file before delivery and start long tables on a new page.
- **A count was stated that did not match the table.** Rule: recount from the table before quoting a number, and compute tallies in code.
- **The LENSAR acquisition was treated as an integration under way when it had been terminated.** The source only confirmed an agreement. Rule: verify that announced deals closed before building on them.
- **The Bengaluru capability centre was missed.** One search was run using the wording of a LinkedIn snippet, found nothing, and the lead was parked. A plain query found it at once. Rule: three differently worded queries across two tools before marking a lead unverified, and re-search when later evidence supports it.
- **Concluded a platform was not in use because the job board had no mentions.** A technology detection source showed it present for years. Rule: absence on one source is a Hypothesis at most.
- **A security incident was found late, through a structured news feed.** Rule: pull the news feed early in every run. Treat threat actor claims as claims and cite the independent catalogue. Never use a breach as an opener.
- **User asked who was at fault in the breach.** Rule: say plainly when nothing is known for the account, separate that from what is reported about the wider campaign, and for Salesforce partner clients frame the issue as identity and integration hygiene around the platform.
- **User said he does not need CISO and security people.** Rule: exclude security, IT compliance and IT audit titles from people lists from now on.
- **User asked to dedupe.** The documents carried four overlapping people sections. Rule: one workbook is the single people list, and the document keeps only a key people table.
- **User asked not to repull rows already loaded.** Rule: continue an existing BitScale source with a pull for the next page, and verify the row count rose by the expected amount before pulling a second source.
- **User lost patience with repeated reminders.** Rule: before delivering, check the whole file against the standing requirements, not only the piece just changed.
- **Tool lessons.** Searching a company name plus conference returns noise, so search by person name. LinkedIn search snippets mix a person's posts with posts they reacted to, so treat them as leads. Firecrawl JSON extraction costs five credits where a plain scrape costs one. Firecrawl returns YouTube transcripts. Workday job boards accept a q parameter on the verified base URL. Sumble matched by domain when the web interface could not find the company by name. BitScale seniority has no Senior Manager level.
- **Sep 24 2026, an earlier account. Second time losing Sumble credits to file_name.** The playbook already banned file_name on people and org searches; the tech stack call was treated as an exception it was not. Rule: the file_name ban covers every Sumble tool. Wider rule: a cost or access lesson about one tool on a server applies to the whole server until proven otherwise. Read the tools playbook before the first paid call, not after.
- **Sep 24 2026, an earlier account. User asked three times for the workspace file.** It is the workspace zip (folder with RESTORE.md, deliverables, build, data, notes) shared on Cambia, not the playbook and not a new workbook. Rule: every delivery ends with the workspace zip in the same present_files call as the deliverables. When the user names a file I do not recognise, search past chats before guessing.
- **Sep 24 2026, an earlier account. Whole Drive search surfaced a WilcoSource owned CitiusTech contract (S31) and the brief carried its budget and counterparties.** User asked whether that would spook the client. Rule: Drive search stays inside named folders; anything client owned that surfaces goes on a do not read list until cleared; contracts are cited at seller level only, in files and in chat. Same lesson as the Cambia folder, made general.
- **Sep 24 2026, an earlier account. User asked for a third party source check before every share.** First run of the new gate found a Grain recording link (S32) and a paid pull (S35) linked to an aggregator page instead of its evidence base. Rule: gate 9a lists every external domain in the files, sorted into account and press, data tools (never), and aggregators (Lead grade only, labeled as aggregator). Internal recordings get no link; paid pulls link to the evidence base, never to a vendor or an aggregator.
