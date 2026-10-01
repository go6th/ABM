# 01 Product and research process (PRD, SAAS)

Use this when implementing what the agents do, what a run must produce, and what the files contain.

## Goal of a run
One company name in; a sourced, graded brief and a deduplicated senior people list out; every claim carries its URL and a grade; a tracker shows what was searched and what was not.

## Inputs
Account name and domain (domain resolved; **careers URL verified by visiting, never constructed**); pursuing client (ask once; if absent, relationship rows are marked as waiting on the account owner and not asked again); platform lanes (default Salesforce incl. field service e.g. ServiceMax, ServiceNow, SAP, Tableau and analytics, Veeva (primary for life sciences, recorded once as None elsewhere), Data and AI); credit cap (one number across tools, within it the run does not stop to ask); people scope (Director and above, Head, Senior Manager; global; **exclude security, IT compliance, IT audit**); optional pre-pulled broker files.

## The 13 steps (map each to a graph node or branch)
1. **Set up**: read checklist and lessons; confirm domain; resolve careers site by visiting; ask once for the pursuing client; check broker balances (free); propose one cap with breakdown; wait for yes.
2. **Pass 1** (cheap relationship and fit): prior customer status and interactions (client CRM, calls, email), alumni and connection overlaps, platform vendor account team (named or marked unknown), profiles of obvious key contacts, basic fit check.
3. **Company baseline**: last three earnings releases or equivalent, newsroom, leadership page; executive voice from the last two earnings call transcripts (focus, opportunity, challenge in the executives' own words with the speaker named). **Every figure quoted is re-read in the release.**
4. **News**: company releases, then the structured feed for third-party items, then targeted searches for anything the feed hints at, phrased as a journalist would. Verify important items at the source article. Unopened feed items are marked **Feed only**.
5. **Jobs**: one live board search per lane keyword (links list, counts); then a 12-month pull of open and closed postings with descriptions, read locally: platform sentence in every posting that names a platform, **full text for every role where the platform is the job**; classify each as **Build role, Admin and owner role, Working tool, or User role**; counts recorded as read vs matched, per platform, open vs closed.
6. **Tech stack**: detections pulled once, lane-relevant rows kept with first-seen and last-seen, cross-checked against postings. **A date range in a detection feed is a lead, not a finding.**
7. **People**: title search on the domain (platform ownership titles plus leadership titles, two seniority passes, paged on the same source); association search only to fill gaps (IT, engineering, product functions); then people named in research with their profile URLs. Merge by profile URL first, name second; exclude; assign level, lane, priority. Full profile pull for every senior person (prior employers, join date, role start, tenure, certifications, location, own description of their work). Posts and comments for **priority A only**, filtered to recent activity. Alumni, tenure and certification counts **computed in code** and stated with their base. People already pulled into a grid are never pulled again.
8. **Person-name searches** (after the people list exists, for the top ten): speaking (past and upcoming), podcasts, YouTube, Medium and Substack, vendor customer stories. Review sites and vendor communities checked and recorded as **None** where nothing is found.
9. **Verification**: every Lead gets three differently worded queries across two tools before it stays a Lead. Parked leads are searched again when later evidence supports them.
10. **Distill**: grade every line; hypotheses written as hypotheses each with a confirming question; first-call questions; decide what changes a decision.
11. **Build**: the standing file set from one builder library so every account looks the same.
12. **Gate**: run every check; fix, rebuild, rerun.
13. **Deliver once**: what was found, what was corrected during checking, what is still open, credits used against the cap.

## Research rules (enforce in code where possible)
- A **keyword match is a lead**, not a finding. Matched postings are read, not inferred from titles.
- A lead is not dead after one search: at least **three differently worded queries across at least two tools** before anything is marked unverified.
- **Absence of evidence is weak evidence**: graded Hypothesis, never a conclusion.
- Every coverage row ends **Done, Partial, Not done, Blocked or None** with proof (queries run, tool, result count). **Silent omission is a defect.**
- Every fact, posting, article and person carries its URL in the same row. Every count names its base.
- Sensitive findings (breach, layoff, lawsuit) are **preparation, never openers**, and the file says so.
- For Salesforce partner clients, nothing is framed as a customer leaving Salesforce or as a Salesforce failure.
- Plain English on every heading; internal program names explained as internal names; abbreviations written out on first use.
- Anything explained in chat about the account is also in a file.

## Deliverables (per account)
Account Summary (Word); Sources and Evidence (Word, every source with stable ID, link, date, grade, the exact sentence relied on); People List (Excel + Summary sheet of exclusions and counts); Job Postings (Excel); Tech Stack and Teams (Excel); Workspace zip (restore note, deliverables, build scripts, raw data with neutral names, notes; client-shareable, passes the same scans); **Internal Only** file (Word, separate; never in the share, never in the zip).

Account Summary order: plain-language narrative first; caveats on interpretation; graded tables lane by lane; key people (priority A and senior); Findings from LinkedIn (separate section next to the people list); executive voice; hypotheses with confirming questions; first-call questions; coverage tracker sorted with the most valuable open work first.

Format rules: Word for documents; US Letter, proper margins; no em dashes; no hyphens or dashes as punctuation; no mid-sentence colon splices.

## Quality gate (blocking, before any delivery)
Every Word file converted to PDF and checked for lost table rows at page breaks; zero orphan citations, banned dashes, stale phrases, excluded names; workbooks recalculated with zero formula errors and totals matching row counts; tracker tally recomputed from rows (never typed); narrative reread against tables (every statement has a source ID and a matching graded row); every number in the chat reply checked against the file; **third-party scan** of the unzipped package (document XML, relationships, workbook cells, properties): zero data broker names, zero credit counts, aggregator sites only on Lead claims labelled as aggregator. SaaS adds a **tenant leakage scan**. On redelivery ship the complete file set with one line on what changed; close open tracker rows that can be closed within the cap first. On gate failure the workflow loops back to the failing step with the defect attached up to a limit, then routes to a 6th operator.

## Constraints and standing rules
Never send outreach; never delete or overwrite operator or client records (new grids, files, rows only); ask before spending beyond the agreed cap and report all spend at the end; client-facing files never name brokers, costs, call recordings, client staff or another account (write "Client"); one account per file set; Drive search only inside folders the client named, client-owned folders that surface are off limits until cleared, contracts cited at seller level only.

## Success metrics (PRD 3.6)
Coverage completeness (every High row Done or None with proof); corrections per run trending to zero for standing rules; claim traceability 100% (every graded row has a link that opens to the claim); turnaround; spend within the cap; seller usefulness (first-call questions used, Confirmed facts referenced), to be instrumented.

## Value figures are assumptions
12 to 20 person-hours manual vs about one working day; about 30 source types vs five to eight manually; a few hundred credits per account. The PRD labels time and value figures as **working assumptions**; never present them as measured outcomes.
