# ABM Account Research: Session Environment and Methods

Companion document to the abm-account-research skill (current version 1.12). Written October 17, 2026.

Purpose: the skill files describe the process. This document describes everything else the claude.ai instance brings to a run that the skill files do not carry: the execution environment, the tool harness, the navigation and verification habits, and the standing user rules that are injected automatically on claude.ai but arrive nowhere else. Give this document to any API, Agent SDK, or other harness running the skill, as a system prompt appendix or as a reference file loaded at the start of every run. Where this document and the skill's own reference files overlap, the skill files win; this document fills the gaps around them.

---

## 1. Why results diverge between instances

The claude.ai run is the sum of three layers. Only the first one travels with the skill file.

1. The skill package: SKILL.md, checklist.md, lessons.md, signals.md, tools.md, output-spec.md, phase1-pull.md, phase2-build.md, plus scripts and assets. Identical everywhere the same version is installed. Verify the version first: v1.12 is current. Running v1.9 or v1.10 against a v1.12 instance guarantees divergence (v1.11 changed the people pull rules and gap check; v1.12 changed drop list application and cross file reconciliation).
2. The execution environment: a sandboxed Linux machine with a persistent per session filesystem, Python, Node, and the ability to run the skill's bundled scripts. Section 2.
3. The injected user context: roughly thirty standing rules that claude.ai loads into every session from the user's memory, before the skill is even triggered. Many are not in the skill files because they predate it or apply beyond it. Section 4 enumerates the ones that shape ABM output.

A harness that has layer 1 but not layers 2 and 3 will produce work that is recognizably the same process but different in numbers, formatting, sourcing discipline, and compliance with the user's rules. That is the inconsistency being observed.

## 2. Execution environment the skill assumes

The skill was written for, and all its quality gates depend on, an environment with these properties.

### 2.1 Filesystem and code execution

- A writable working directory that persists for the whole run. Every raw tool response is saved to a file the moment it arrives, named by account, tool, and query, before any interpretation happens. The account folder layout is: raw pulls in a raw subfolder, a pull log (query, tool, parameters, result count, timestamp per pull), build scripts, notes, and the five deliverables at the top. The folder ships as {Account}_workspace.zip with a RESTORE.md at its root.
- Python 3 with pandas and openpyxl, and Node with the docx library, available for building files and for all computation. The bundled assets (docx_lib.js, people_workbook_example.py, jobs_workbook_example.py, platform_ranking.py) are run, not treated as documentation.
- The pre delivery gate scripts are executed for real: rowcheck.py (renders each .docx to PDF and confirms no table row was dropped at a page break), doc_checks.py (every source ID has a link, banned punctuation, stale phrases, excluded names), strip_tool_names.py (replaces data tool names with 6th agents in build sources and checks the rendered files), third_party_scan.py (lists every external domain inside the rendered files, including document XML, hyperlink relationship ids, and workbook cells).

### 2.2 Compute, never estimate

This is the single largest driver of answer consistency. On claude.ai, no count, ranking, dedupe, tenure, alumni figure, or platform tally is ever produced by the model reading rows in its context window. The procedure is always:

1. Save the raw data to files.
2. Write a short script (pandas or plain Python) that computes the number from the files.
3. Report the script's output, and state the base the count is over (open vs closed postings, deduplicated people vs profile records, point in time vs any point in a career).

A model that answers "how many Salesforce postings" by skimming retrieved rows will drift run to run and disagree with the workbooks. A model that greps and counts over the same files will match every time. If the target harness has no code execution, see section 5.2, because parity on numbers is then not achievable and the service model has to change.

### 2.3 How the skill files are loaded during a run

Load order matters because each file changes behavior at a specific step:

- SKILL.md at trigger.
- references/checklist.md read in full at the start of every run, never from memory of a prior run. It holds the standing requirements, the coverage rows that become the tracker, and the pre delivery gate.
- references/lessons.md skimmed at run start.
- references/tools.md before the first tool call.
- references/signals.md before Pass 2.
- references/phase1-pull.md or phase2-build.md only in split mode, at the split.
- references/output-spec.md before building any deliverable.

An API harness that stuffs all eight files into one prompt up front will work, but must still treat checklist.md as the authority on coverage and the gate, and must not summarize or truncate it.

### 2.4 Model split

On claude.ai the default since September 27, 2026 is split mode: phase 1 pulls on Opus class, phase 2 sense check, analysis, and deliverables on the strongest available model (currently Fable class). Phase 1 is deliberately mechanical (v1.10 rewrote phase1-pull.md with exact call parameters, expected result ranges, retry rules, and a completeness gate precisely so a rule following model can execute it). Phase 2 is judgment heavy: entity boundaries, hidden titles, grading, hypothesis writing. If the API deployment uses one model for everything, use the strongest one and still run the phase 2 sense check step; do not let a pull optimized model write the analysis.

## 3. Tool harness and navigation methods

### 3.1 What is connected on claude.ai

These MCP servers are authenticated and available in the claude.ai sessions, with these roles in the ABM run (routing detail is in tools.md; this is the roster the API harness must match):

- Sumble: technology adoption, people by technology association, teams, org tech stack, SQL over its tables. Has a hard gate: any call it prices above 500 credits is refused until explicitly confirmed, one call at a time. Never set file_name on any Sumble call; vendor hosted files are unreachable from the sandbox and the credits are lost. Results come back inline and are saved locally.
- PredictLeads: structured news events, technology detections, job openings with full descriptions (open and closed, 12 months, paginated), financing, connections. Called by domain; large responses land in files and are parsed locally.
- Firecrawl: page scrapes (one credit, plain format, parse locally; never the five credit JSON or query formats on job pages), live career site sweeps (verified base URL plus q= keyword, formats links, waitFor 4000, maxAge 0), YouTube transcripts via the watch URL, site maps.
- Parallel Search: free web search with long excerpts (objective sentence plus three or four keyword queries, one session id per run) and free single page fetch with an objective. First choice for open web reading.
- BitScale: people by title. Has a mandatory consent flow (ask_human_v1, then plan_bitscale_workflow_v1, before any write), strict schemas (parameter names and catalog values looked up, never guessed), and the v1.11 rule: people sources get person_titles and q_organization_domains only, never seniority or department filters, because the filters intersect and destroy coverage (tested: 63 matches with titles alone vs 5 with seniority added, same company).
- MoltSets: email and phone resolution from LinkedIn URLs, with risk grades. Used only when the user asks for contact details.
- Google Drive: the ABM Research folder (one subfolder per account) is where deliverables, workspace zips, and Sumble pre pulls live. Account files are read from there rather than asking for uploads. Drive search stays inside folders the client named; any client owned folder that surfaces is off limits until cleared.
- Gmail, Google Calendar, Grain: client history pass only (Pass 1), and only once the pursuing client is named. In split mode this pass runs in phase 2 if phase 1 ran on a harness without these connectors.

claude.ai also has a built in web search and web fetch as free fallbacks. If the target harness lacks them, Parallel covers both roles.

If a server from this roster is missing in the target harness, the corresponding coverage rows are marked Blocked with the reason, never silently skipped and never faked from general knowledge or training data. A row answered from the model's memory instead of a live pull is a process failure even when the answer happens to be right.

### 3.2 Navigation habits that are not written in the tool schemas

These are behaviors the claude.ai instance applies on top of tools.md, and they matter for consistency:

- Schema discipline. Parameter names and enum values are copied from the tool's own schema or lookup endpoints (Sumble LookupTechnologies, BitScale list_source_types_v1 and get_tool_schema_v1, PredictLeads documented params). Nothing is guessed or translated. A guessed filter value that silently matches nothing is the main way two runs return different totals from the same tool.
- Pagination to exhaustion, checked against expected ranges. PredictLeads job pulls page at 1,000 per page, three pages for a year at a large company. BitScale people sources load in blocks of 100 and are continued with pull_latest_source_data_v1 until matched_upstream is reached. phase1-pull.md carries expected result ranges per pull; a result far below range triggers the retry rule, not acceptance.
- Sanity thresholds. A BitScale title search returning under 15 matches at a company above 500 employees means the query is wrong (check for a stray seniority or department filter and the domain), rerun once, and log both counts.
- The three query rule. Before anything is marked unverified, at least three differently worded queries across at least two tools, phrased the way a journalist would describe the event (company, city, plain nouns), never the wording of the snippet that raised the lead. Parked leads are re searched when later evidence supports them.
- Read before counting. Keyword matched postings and articles are opened and read, then classified (core, working tool, tools list, false positive) before entering any tally. Former employer recruiting boilerplate is a named false positive class (v1.12). A keyword tally is never reported as adoption.
- Every pull logged. The pull log records tool, exact parameters, result count, and file written, so phase 2 and any later question can reproduce the pull. This log is what makes "why does the workbook say 57" answerable.

### 3.3 Link verification

Every URL that will appear in a deliverable is visited live during the run (Parallel fetch or Firecrawl) before it is cited. If the page no longer resolves (closed requisitions, expired listings), the file says "Archive record" instead of printing the dead link, signaling the reader to ask for the captured evidence. Career portal and ATS URLs are taken from the site itself, never constructed from a guessed tenant slug. Every factual claim is checked to actually appear in the source cited for it; a narrative is never attributed to a source that does not contain it, and interpretations are labeled as such in the file.

## 4. Standing user rules injected on claude.ai

On claude.ai these arrive automatically from the user's memory in every session. An API instance sees none of them unless they are in its prompt. They apply to every ABM run and every answer about an account. Several duplicate checklist.md rules; the duplication is intentional so this document stands alone.

Formatting and voice:

1. Deliverables are Word (.docx) and Excel (.xlsx) files with proper margins and human readable formatting, never raw markdown files.
2. No em dashes anywhere, in any form, and no hyphens or dashes used as punctuation. Rewrite with colons, commas, or full sentences. When rewriting, avoid mid sentence colon splices; restructure into full sentences. Colons introducing lists are fine.
3. Never the bold lead in bullet pattern (a bolded header phrase followed by plain text on the same bullet). Plain bullets only.
4. Never the phrase "our read" or any "it is a read" labeling formula, under any circumstances. Label interpretations in plain language, for example "We believe".
5. Plain English on every heading: no jargon a busy executive would decode, abbreviations written out on first use, internal program names identified as internal names.
6. Every count names its base.

Confidentiality and attribution:

7. Client facing files never name data tools or vendors (the roster in section 3.1 included) or their credit costs. Attribution where needed is "6th agents" or "6th (my agents)".
8. Before sharing, run the third party source check: list every external domain the files link to or name, including inside the unzipped document XML, hyperlink ids, and workbook cells; confirm zero data tool names; aggregator sites appear only on Lead grade claims labeled as aggregator.
9. No names of the user's or the client's account team anywhere Claude writes. No client staff names in "who to ask" or next step lines; write "Client". Getting names from the account team is the user's job; never draft those asks unrequested.
10. No references to internal call recordings in any deliverable or workspace file, not as sources, links, or rows. If something important comes from a call or similar internal source and names client people or should not leave the user, it goes in a separate document marked "Rajesh Only", kept out of the client share and the workspace zip.
11. Keep other accounts out of an account's files and out of chat about that account. Contracts found in Drive are cited at seller level only: no counterparties beyond the client, no budgets, signatories, or document numbers, in files and in chat.
12. Sensitive findings (breach, layoff, lawsuit) are preparation, never outreach hooks, and the file says so.

Scope and data rules:

13. Never search or build findings on ServiceMax, Siebel, or Databricks. Always include MuleSoft in platform keyword searches. The standing lanes are Salesforce, ServiceNow, SAP, Tableau, Veeva, MuleSoft, and Data and AI, on every account in every industry.
14. People lists: Director and above, Head, and Sr. Manager; no CISO or security people; deduplicated across sources, never re pulling people already pulled.
15. Every organization row in any contact or target list carries a revenue band or an employee band, ideally both, with a size source column. Never guess a figure; if none exists, say so and explain why the row is included. When the figure is a parent company's, name the parent and flag in a field that the size is the parent's.
16. The Tech Stack and Teams workbook ranks the popular platforms seen in the account's postings A, B, C.

Process and delivery:

17. One credit cap for the whole run is agreed up front with a short breakdown. Within the cap, run without stopping to ask; anything beyond it comes back first; total spend reported at the end. Paid, complete work beats free, partial work; balances are checked before starting.
18. Drafts only: no email, message, or any client facing communication is ever sent; create drafts and stop. Nothing is deleted or overwritten: new grids, new files, new rows, and explicit confirmation before removing anything.
19. The standing deliverable set per account: Account Summary (Word, graded, with narrative and coverage tracker), Sources and Evidence (Word), People List (Excel), Job Postings (Excel), Tech Stack and Teams (Excel), and the workspace zip. No Executive Brief deck unless explicitly asked.
20. Every re delivery after a fix or follow up ships the complete file set plus the zip (and any Rajesh Only file separately), with one line naming which files changed. A re shared file opens with one line on what differs from the last version. Open tracker rows that can be closed within the agreed cap are closed before delivering, not left for later.
21. Anything explained in chat about an account must already be in a file when the files are delivered.

## 5. Answering questions against an existing workspace

This is where the October 15 and 16 inconsistencies were observed, so it gets its own rules.

### 5.1 With code execution available

- The RESTORE.md inside each workspace zip pins the canonical query definitions (what counts as a Salesforce posting, which base each number is over, the drop lists). Those definitions are authoritative; apply them before answering, even when the question is phrased loosely.
- Numbers come from code run over the workbooks and raw files in the zip, never from reading retrieved rows. Unzip, load with pandas, apply the pinned definition, report the computed figure and its base.
- Drop lists (false positive exclusions) apply to every file carrying the claim, and per platform counts must reconcile across all files before any answer or delivery. If two files disagree, say so plainly, state which is wrong and why, and fix it; never average or pick silently. This is the v1.12 rule, added after a workbook showed 57 rows for a platform while the ranking sheet said 13 because a drop list had been applied to one file only.
- When a strict query returns zero results, the correct answer is: report zero for the query as asked, then show the nearest matching set with the relaxed condition explicitly labeled as relaxed. Never substitute the relaxed set for the strict answer, and never return nothing.

### 5.2 Retrieval only harnesses

A harness that can only retrieve indexed chunks of the workspace, with no code execution, cannot reach parity on any computed number and should not try. For that deployment:

- Keep the index fresh. An outdated zip in the index produced confidently wrong answers; re index on every re delivery and record the workspace version in the index.
- Feed it the precomputed digest files (the .md and .xlsx digests produced for exactly this purpose) and constrain it to quoting figures from the digests and RESTORE.md definitions. Any number not present in a digest is answered "not computed in this workspace" plus an offer to route the question to the computing instance, never estimated from retrieved rows.
- Apply the same zero result rule, the same base naming rule, and the formatting rules in section 4.

### 5.3 Output rendering

Claude renders tables and structure as Markdown. The claude.ai app renders it; a custom frontend must either render Markdown (tables included) or the harness must instruct the model to return plain text or HTML. A frontend showing raw Markdown as one garbled string is a frontend issue, not an answer issue, but it reads as inconsistency to the user, so fix it on the frontend or change the requested output format in the system prompt.

## 6. Parity checklist for any new deployment

Before comparing results, confirm all of these. Each unmet item is a known source of divergence.

1. Skill version is 1.12, complete (all seven references, three scripts, six assets), loaded per section 2.3.
2. Code execution and a persistent per run filesystem exist, and every number is computed from saved files (section 2.2).
3. The MCP roster in section 3.1 is mounted and authenticated, or missing rows are reported Blocked, never improvised.
4. The standing rules in section 4 are in the system prompt verbatim.
5. The pre delivery gate scripts actually run, including the third party domain scan and tool name strip.
6. Split mode is preserved, or a single strong model runs everything including the phase 2 sense check.
7. A credit cap is agreed at run start and spend is reported at the end.
8. Workspace question answering follows section 5, with RESTORE.md definitions pinned and cross file reconciliation enforced.
9. The frontend renders Markdown, or the prompt specifies a different output format.
10. Every cited URL was visited live this run; dead links appear as "Archive record".

When a divergence is still found after all ten hold, capture both answers, the exact question, and the workspace version, and bring them back for a skill update: corrections live in checklist.md and lessons.md, and the skill version increments. Corrections that live only in chat are lost.
