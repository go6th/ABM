# Output specification

Standing files per account, named with the account first: `{Account}_Account_Summary.docx`, `{Account}_Sources_and_Evidence.docx`, `{Account}_People_List.xlsx`, `{Account}_Job_Postings.xlsx`, `{Account}_Tech_Stack_and_Teams.xlsx`, `{Account}_workspace.zip`. An `{Account}_Executive_Brief.pptx` is built only when the user asks for it. The summary and evidence files are for the user; the workbooks and the zip are what the user shares with the client, so they carry no source IDs, no tool names and no credit costs. Read the docx and xlsx skills before building. Reuse `assets/docx_lib.js` so every account looks the same.

## Writing rules for all files

- No em dashes, and no hyphens or dashes used as punctuation. Restructure the sentence instead. Do not replace a dash with a mid sentence colon. Colons that introduce a list are fine.
- Sanitize text that comes from tools, since titles and summaries often contain dashes.
- Every claim must appear in the source cited for it. Narrative sentences carry source IDs in brackets, for example (S9, J2).
- Label interpretation as interpretation, in a heading or in the sentence.
- Plain words. No filler. Short paragraphs.
- Landscape US Letter for table heavy documents, one inch margins, Arial.
- Start long tables on a new page. Keep header rows repeating.

## Grades

| Grade | Meaning | Use in outreach |
|---|---|---|
| Confirmed | Read in a primary source during this run | May be stated |
| Probable | Inferred from one or two sources, reasoning given in the row | Ask, do not state |
| Hypothesis | Synthesis, no source states it | Ask, do not state |
| Lead | Seen in a secondary source, not verified | Do not use until checked |

Read depth for news: Read, Headline only, Feed only.

## Account Summary, section order

1. Title block with research date and a pointer to the evidence file.
2. How to read this file: the grades.
3. **Narrative, in plain words.** Subsections: the account picture, what stands out, what it likely means for each lane with interpretation labeled, how this could be wrong, the simplest way to sharpen it.
4. **Recent news.** Reverse chronological table: date, what happened, read depth, source, why it matters. Then a line on what the timeline does not cover.
5. Company and transformation baseline.
6. **Signals:** Tier 1, Tier 2, Tier 3, then context and history.
7. **Platform picture** by lane, each row graded with reasoning.
8. **People.** How the list was built, the counts, the exclusions, a key people table of senior priority A people with links, and interpretation bullets. Point to the workbook for the full list.
9. **Hypotheses to test.** Numbered, each with a confirming question. Include cautions for sensitive findings.
10. Questions for a first conversation.
11. **Research coverage tracker.** Decisions waiting on the account owner first. Then Pass 1 and Pass 2 tables with columns Source type, Status, Priority, Needs, What was done, What remains, sorted High first and with rows needing nothing ahead of rows that wait on someone. Then a tally computed from the rows.
12. **Source links.** ID, source name as a link, domain.

## Sources and Evidence, parts

- A: Source register. ID, source with link, type, date, how retrieved, what it supports. Tools and paid data pulls get their own IDs.
- B: Job posting extracts for postings read in depth.
- C: Postings read one by one for each primary lane, with a classification column.
- D: Video and article extracts.
- E: Job board captures, every listing with its link and whether it was read.
- F: Leads that are not verified, including the queries tried.
- G: How to keep copies of the original pages.
- H: Data pulls, such as technology detections by lane, matching postings and the full news event list.
- I: Full URLs written out, for printing.

People are not listed in this file.

## People List workbook

- Sheet Summary: what the list is, sources and dates, the seniority rule, who was excluded and why, and formula driven counts by level and by lane and priority.
- Sheet People: No., Name, Title as reported, Level, Meets seniority rule, Lane, Priority, Former SI employers (firm and years), Found by, LinkedIn link, second profile link, Note. Filterable, header frozen, priority color coded.
- Counts use formulas so the user's corrections flow through. Recalculate and confirm zero errors.

## Job Postings workbook

- Sheet Summary: scope and dates, class definitions, formula driven counts by platform, status and class.
- Sheet Jobs: requisition, title, platforms named, class, status, first seen, last seen, location, seniority, what the posting says for roles read in full, the exact platform sentences, the full description, link. One row per posting.

## Tech Stack and Teams workbook

- Sheet Platform Ranking, first sheet. Every platform named in the account's job postings from the last 12 months, ranked A, B or C by how many distinct postings name it. Build it with `assets/platform_ranking.py` from the same postings file the jobs workbook uses, so the two workbooks share one base.
- Base: postings in the last 12 months, open and closed, that name at least one platform in the dictionary. State the base on the sheet. Each posting counts once per platform.
- Rank rule: A is named in 15 percent or more of the base and in at least 5 postings. B is named in 5 percent or more and in at least 3 postings. C is named in at least 2 postings. Platforms named once go under Seen once, unranked. The rank cell is a formula on the count and share cells.
- Columns: Rank, Platform, Category, Lane (or Outside the lanes), Postings naming it, Share of base, Open now, Last seen, In confirmed stack, Matches checked by reading.
- Covers platforms outside the six lanes too (Workday, Snowflake, the clouds, Power BI and the like), because what else the account hires for shapes the pitch. Databricks, Siebel and ServiceMax are never listed.
- Before ranking, read at least three matching postings for every platform that would rank A and drop false positives (pass them with `--drop`). Pass the platforms you read with `--checked`.
- The sheet says that hiring for a platform is a signal of skills wanted, not proof the platform runs in production, and shows whether the confirmed stack pull also has it.
- Then the confirmed stack sheet as before, vendor links stripped.
- Sheet Platform teams: Team, Lane technology, Jobs count, First activity, Last activity, Team technologies (every technology the teams pull lists for the team, with its jobs count, as a comma separated cell), Team locations (city and country with jobs count), Job functions. The technologies and locations columns are read in phase 2 for two things: what sits next to the lane platform in each team, and whether the team is onshore, offshore or a captive. Vendor links stripped.

## Owner Only file (only when there is something to put in it)

`{Account}_the owner_Only.docx`. Created only when a call, email thread or internal conversation surfaced something important that names client or account team people, quotes what they said, or otherwise should not leave the user. Cover and header read "Owner Only". Short: the finding, where it came from ("call notes, date"), why it matters, and what the client facing files say instead. Kept out of the client share and out of the workspace zip, and named on its own line in the delivery note. Everything else about tool names and links still applies to it.

## Executive Brief deck (only on request)

Not a standing deliverable. Built only when the user asks for it in the current conversation. When asked, built with pptxgenjs following the pptx skill. Cover, the account in one page, the numbers with a native chart, what the executives said (focus, opportunity, challenge), industry, market and account specific challenges, what runs the business, signals, who owns what with names, where the client can add value mapped to the client's published capabilities, what could make it harder with incumbents named, platform hiring over 12 months table, what a year of postings tells us, what the profiles tell us, other things worth knowing, next steps. Every content slide has notes in three blocks: confirmed, what we are guessing, what we have a hunch on. No source IDs on slides. Plain English headlines. Validate, render and inspect every slide before delivery.

## Findings from profiles section in the summary

Sits right after the key people table. A graded table of findings with what the profiles show and our interpretation, then a table of people whose status changed, then any credential callouts the user asks for. Alumni counts state that they cover any point in a career.

## ID scheme

S for sources, J for job postings read, U for postings that could not be loaded. Keep one URL map per account and generate the link tables from it, so a citation can never point to a missing link. Reuse IDs on refresh.

## The delivery note in chat

Short. What was found that matters, what the checks caught and fixed, what is still open and what it needs, credits used. Every number in the note must match the files.
