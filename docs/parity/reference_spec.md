# The owner's reference output set, measured 2026-10-29

The golden example: an account research finished by the owner's Claude.ai research skill on
2026-10-20 (three workbooks, three Word documents, a phase 1 zip and a workspace zip), kept by
the owner at `~/Downloads/BD-20261025T051443Z-1-001/BD/`. Read those files only with `python -I`
(untrusted downloads). In this repository the account is called "the reference"; `{prefix}` below
is the account's short name (it prefixes file names and the Entity column). Its name must never
appear in code, config, fixtures or tests (owner rule 2026-10-27, `parity-for-every-workspace`).
`docs/parity/reference_manifest.json` has the sheet order, header rows, headers and widths.

## Common workbook look
Arial 10; header row bold white on navy `1F3A5F`, vertical center, wrap; body cells thin border
`BFC5CC`, vertical top; hyperlink cells blue `1155CC` underlined; titles Arial 14 bold navy; notes
merged across the table, wrapped, row height set. Formulas are stored as formulas and re-saved by
LibreOffice so cached values exist.

## Job Postings workbook: sheets Summary, Jobs
Jobs: 14 columns, in order, with widths:
Requisition 12 | Title 44 | Platforms named 18 | Class 20 | Status 9 | First seen 11 | Last seen 11 |
Location 26 | Seniority 11 | Entity 26 | What the posting says (read) 50 | Platform sentences 70 |
Full description 60 | Link 13. Freeze `C2` (panes xSplit 2 ySplit 1), filter over the table, rows
sorted by First seen descending.
* Requisition: the id printed after "Job ID" in the posting text (blank on 1,041 of 1,897 rows);
  never made up from the URL.
* Platforms named: comma list in the FIXED order Salesforce, MuleSoft, ServiceNow, SAP, Tableau,
  Veeva, Workday.
* Class: `Build role` (the platform is the job), `Admin and owner role` (runs/owns/governs it),
  `Working tool` (daily use), `User role` (everything else incl. boilerplate and tool lists).
  Counts: Working tool 1324, User role 515, Admin and owner role 31, Build role 27.
* Status: `Open` or `Closed` only (no third word). First seen / Last seen: ISO date text
  `2026-10-19` from the feed. Seniority: the feed word as is (`mid_senior`, `junior`, `manager`,
  `director`, `vice_president`, `head`, `partner`), blank when absent.
* Entity: `{prefix}` for the account's positions; a tagged divested business or subsidiary gets its
  own label (rules are per account data: text patterns plus an optional "first seen before" date).
* What the posting says (read): filled only for rows read by hand (Build/Admin/some Working tool,
  120 of 1,897).
* Platform sentences: `Salesforce: s1 | s2 || SAP: s3`, up to 4 sentences per platform, 3,000
  characters at most, never a newline; `(named in the title only)` when only the title matches.
* Full description: the posting text with markdown marks removed, at most 32,000 characters.
* Link: the cell text `Open posting`, the hyperlink is the posting URL (every row has one).
NEW (owner request 2026-10-29): after Link add `Archived link` (text `Open archived copy`, hyperlink
to our stored copy, absolute address on the web origin) and `New since last research`.
Summary: A1 title `{prefix} platform job postings, last 12 months (first seen D1 to D2)`; five note
paragraphs merged A:H (height ~46) on: what the rows are, the Entity meaning, the Class meaning,
what the read/sentence/description columns hold, the platform matching rules; row 10 navy header
`Platform (named anywhere), {prefix} positions only | Total | Closed | Open | Build role | Admin and
owner role | Working tool | User role`; rows 11..17: Salesforce, MuleSoft, ServiceNow, SAP, Tableau,
Veeva, Workday, each cell a COUNTIFS over Jobs, e.g.
`=COUNTIFS(Jobs!$C$2:$C$N,"*Salesforce*",Jobs!$J$2:$J$N,"{prefix}*")` plus
`Jobs!$E$2:$E$N,"Closed"` / `"Open"` / `Jobs!$D$2:$D$N,"Build role"`; row 19 `Rows on the Jobs sheet
(all entities)` `=COUNTA(Jobs!$B$2:$B$N)`; row 20 `Rows that are {prefix} positions`
`=COUNTIF(Jobs!$J$2:$J$N,"{prefix}*")`; row 21 rows minus those. Column A width 52, B..H 18.
How the rows are made: platform regexes over title+description (SAP case sensitive):
Salesforce `salesforce|\bsfdc\b|sales cloud|service cloud|marketing cloud|health cloud|experience cloud|commerce cloud|field service lightning|agentforce|salesforce cpq`;
MuleSoft `mulesoft|anypoint`; ServiceNow `servicenow|service now\b`;
SAP `\bSAP\b|S/4 ?HANA|S4HANA|\bAriba\b|SuccessFactors|\bConcur\b`; Tableau `tableau`;
Veeva `\bveeva\b|vault crm|promomats|medcomms|vault clinical|vault rim|vault quality|vault safety|veeva vault|\betmf\b|\bctms\b`;
Workday `\bworkday\b`. Only postings naming one of them are rows (open and closed). Class for rows not
read by hand: `Working tool` when the platform sentences (window: whitespace collapsed, previous
"." or "*" to next ".") contain any of experience, proficien, knowledge, familiar, working with,
using, utiliz, document, enter, update, maintain, input, record, log, skills, transactions, data
entr, administ, manage (substring, case-insensitive), else `User role` (measured 100% on the 1,777
non-hand rows).

## People List workbook: sheets Summary, People
People: 13 columns, widths 6 | 26 | 56 | 22 | 11 | 30 | 9 | 30 | 30 | 13 | 13 | 24 | 50:
No. | Name | Title as reported | Level | Meets seniority rule | Lane | Priority | Former SI and
consulting employers (firm and years) | Found by | LinkedIn | Second profile | Location | Note.
Freeze `C2`, header height 32, sorted by level, priority A/B/C, lane, name. 736 rows: Meets
seniority rule Yes 424 / No 312. Levels (9): C-level, EVP and SVP | VP | Senior Director | Director |
Associate Director | Head | Senior Manager | Manager | Lead, owner, specialist or developer. Lanes
(8): Salesforce and commercial | Salesforce (MuleSoft and integration) | ServiceNow and shared
services | SAP and ERP | Veeva and life sciences systems | Analytics, data and AI | Technology,
digital and IT leadership | Business leadership and other. Priority cell fills A `DDEFE0`, B `FFF1CC`,
C `EEEEEE`, centered. LinkedIn cell text `Open profile` with the profile URL (735 of 736).
Found by: the search source string (`title search source 1 (leadership and platform)`, `title
search source 2 (manager)`, `technology association search (Tableau)`, `Leadership page; profile
found by search`), joined `; ` when several.
Rules (one lane per person, first hit in order; titles lower-cased):
Level (first hit): `svp|evp|chief |cio|cto|ceo|cfo|senior vice|executive vice` C-level; `vice
president|vp` VP; `senior director|sr.? director|executive director` Senior Director; `associate
director|assoc.? dir` Associate Director; `director|dir` Director; `head|global lead` Head; `senior
manager|sr.? manager|senior (it|program|product|project|engineering|technical product) manager`
Senior Manager; `manager|mgr` Manager; else Lead, owner, specialist or developer. Senior = level at
or above Senior Manager.
Lane (first hit): Salesforce and commercial = salesforce|sfdc|crm|commercial
(technology|systems|excellence|operations|effectiveness)|sales (technology|operations|enablement|effectiveness)|revenue (operations|technology)|customer (engagement|experience)|cpq|e-?commerce|digital commerce|marketing (technology|automation|operations);
MuleSoft and integration = mulesoft|middleware|api|enterprise integration|solution integration|technical integration|integration (engineer|architect|platform|developer|lead|engineering);
ServiceNow and shared services = servicenow|itsm|service management|shared services|global business services|gbs|service delivery|hr (technology|systems|service)|workday|hris;
Veeva = veeva|vault|medical affairs systems|regulatory systems|quality systems|clinical systems|promomats|mlr;
SAP and ERP = sap|s/4|erp|master data|mdm|order management|order to cash|supply chain (systems|technology|digital)|plm|abap|basis;
Analytics, data and AI = tableau|analytics|insights|visualization|bi|business intelligence|data (product|engineering|science|scientist|platform|governance|architect|management|strategy|analytics)|ai|machine learning|artificial intelligence|decision science|data and|data &;
Technology, digital and IT leadership = digital|technology|information|it|architect|software|cloud|devops|infrastructure|enterprise applications|applications|platform|automation|cyber; else Business leadership and other.
Priority (title only): C when the title matches a non-IT function list (sales rep/territory/account
manager, clinical, marketing manager, brand, scientist, r&d, research, field service, plant,
manufacturing, quality engineer/assurance, regulatory affairs, medical director/affairs, finance,
accounting, tax, treasury, legal, counsel, communications, public/government affairs, investor,
executive assistant, talent, recruit, learning, payroll, benefits, hr business partner, human
resources, general manager, retired, emeritus) AND no platform word (salesforce, sfdc, servicenow,
sap, veeva, master data, crm); B for M&A/business-integration phrases; A for platform names, chief
information/digital/technology/data, VP of IT/information/technology/digital/data/analytics,
head of IT, commercial technology/systems, sales/revenue technology/operations, enterprise
applications/architecture/data/ai, data engineering/platform/governance/architecture, analytics,
digital transformation/solutions/technology/platforms/excellence, integration/middleware/api
architect, shared services/gbs/service delivery, quality/clinical/regulatory systems, e-commerce,
cpq, order to cash, supply chain systems, ai, global business solutions, revenue officer, supply
chain officer, direct to consumer; else B. Below-rule people are kept (Meets seniority rule "No")
only when the title names a platform or technology.
Exclusions: employer domain not the account's; titles matching security|cyber|it
compliance|audit|privacy officer.
Former SI employers: from profile experiences matched to 28 firms (Accenture, Deloitte, PwC, EY,
KPMG, Capgemini, Cognizant, Infosys, TCS, Wipro, HCL, IBM, McKinsey, BCG, Bain, Tech Mahindra,
LTIMindtree, Genpact, DXC, Slalom, Persistent Systems, CitiusTech, Virtusa, Hexaware, NTT Data,
Mphasis, EPAM, Globant), printed `Firm (2015 to 2019); Firm (2020 to date)`.
Summary: A1 title `{prefix} people list, deduplicated`; six notes A3..A8 merged A:F (height 58);
row 11 navy header `Level | People | Priority A`; rows 12..20 the nine levels
(`=COUNTIF(People!$D$2:$D$N,A12)`, `=COUNTIFS(People!$D$2:$D$N,A12,People!$G$2:$G$N,"A")`), row 21
Total, row 22 `Meets seniority rule` `=COUNTIF(People!$E$2:$E$N,"Yes")`; row 24 header `Lane, senior
people only | A | B | C | Total`; rows 25..32 the eight lanes (COUNTIFS by lane, priority and
`Yes`), `=SUM(B25:D25)`; row 33 Total. Column A width 44, B..F 12.

## Tech Stack and Teams workbook: sheets Platform Ranking, Confirmed stack, Platform teams
Platform Ranking: freeze `A9`, filter `A8:J<last>`. A1 title `{prefix}: platforms seen in job
postings, ranked A, B, C`; A2 `Postings in the last 12 months that name at least one platform (the
base):` with the base count in F2; A3..A6 four notes; row 8 header Rank | Platform | Category | Lane |
Postings naming it | Share of base | Open now | Last seen | In confirmed stack | Matches checked by
reading. Rank is `=IF(AND(F9>=0.15,E9>=5),"A",IF(AND(F9>=0.05,E9>=3),"B",IF(E9>=2,"C","")))`, Share
`=IF($F$2=0,0,E9/$F$2)`. Open now = postings not closed; Last seen = max last-seen date; rank fills
A `C6EFCE`, B `FFEB9C`, C `E7E6E6`. Then a `Counts` block (`Rank A/B/C` `=COUNTIF($A$9:$A$N,"A")`)
and a `Seen once (not ranked)` list (platform, category, lane). Widths A 8, B 30, C 26, D 22, E 12,
F 10, H 12, I 14. 31-platform dictionary with categories and lanes (lane labels: the six lane names,
"Tableau and analytics", "Data and AI", "Outside the lanes"); Databricks, Siebel, ServiceMax never
listed.
Confirmed stack: freeze `A5`, filter `A4:E<last>`; A1 title, A2 italic note merged A:E; header row 4:
Business function | Category | Technology | Postings naming it | Lane; rows from the organisation
tech stack pull (business function -> category -> technology with `job_post_used_count`); widths
26, 40, 34, 18, 26.
Platform teams: freeze `A5`; A1 title `{prefix}: teams whose public records name a lane platform (25
largest of N matching teams, by postings since D)`; A2 note; header row 4: Team | Lane technologies
named | Postings count | First activity | Last activity | Team technologies (name, postings) | Team
locations (place, postings) | Job functions; widths 40, 30, 10, 12, 12, 70, 50, 40; body wraps.

## Word documents
Letter landscape (0.9 in top/bottom, 1.0 in sides), Arial 10, real heading styles, a page break
before each level-1 heading, navy table headers, grade fills (Confirmed `DDEFE0`, Probable `FFF1CC`,
Hypothesis `FBDAD4`, Lead `E6E6E6`; the same four for Done/Partial/Not done/Blocked/None), 156
hyperlinks in the Summary (Key people "Profile" links, source list) and 278 in Sources and
Evidence (every source and posting row), footer with the page number only. Summary sections 1 to 10;
Evidence parts A to I.

## Zip layout
`RESTORE.md`, `deliverables/`, `data/` (raw pulls as JSON plus `data/releases/` one text file per
page read: front matter `title:`, `url:`, `date:`, `source: 6th agents`, then the text), `build/`,
`notes/`.

## Owner requests of 2026-10-29
1. Exact copy of this shape for every workspace; 2. every link a working hyperlink with a second
"archived copy" link (our stored copy, JSON/text); 3. re-research pulls only what is new since the
last research, keeps every earlier row, flags the new rows; 4. web tables mirror the Excel files
with the links.
