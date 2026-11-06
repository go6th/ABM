# Audit of BD_workspace.zip (the BD golden reference), 2026-11-03

File audited: `~/Downloads/BD_workspace.zip` (43.4 MB, 366 files, all dated 20 October 2026). It is
the hand-built BD (Becton, Dickinson and Company) research workspace that the product's exports are
rebuilt to match. Nothing in the zip was changed. Every file was opened: the four Word files were
read in full (and rendered to PDF to check the real pages), every sheet of the three workbooks was
dumped and recomputed row by row, the notes and build scripts were read, the 25 data JSONs were
profiled and the 302 captured source pages were scanned. Quote checks against the captured sources
were sampled (37 claims plus 16 figures); everything else ran over all rows.

## 1. What is in the zip

| Folder | Files | What |
|---|---|---|
| `ws/deliverables/` | 5 | The client set: Account Summary (34 pages), Sources and Evidence (60 pages), People List (736 rows), Job Postings (1,897 rows), Tech Stack and Teams (3 sheets) |
| `ws/BD_Owner_Only.docx` | 1 | Owner-only notes (1 page) |
| `ws/notes/` | 3 | Phase 1 pull log (every paid call), pass 1 internal history, phase 2 sense check |
| `ws/data/` | 25 + 302 | Raw pulls (jobs 50 MB, profiles 11 MB, posts 10 MB, detections, teams, searches) and `releases/` (116 releases, 92 live postings, 69 person pages, vendor pages, 4 transcripts, 10-K Item 1) |
| `ws/build/` | 29 | 11 Python and 4 JS scripts plus intermediates; `RESTORE.md` says how to rebuild |

The deliverables were built by the scripts from the data; the counts in all three workbooks, the
Account Summary and the Evidence file reproduce exactly from the rows (checked). All 162 Excel
formulas carry cached values. No em or en dash, tool name, client name, `undefined`, `null` or
placeholder text appears in any Word file. Every hyperlink in the Word files has a target.

## 2. Verdict

The deliverables are internally consistent and well sourced, but the zip as a whole is not
client-shareable, two figures in the Summary are unsupported, and each workbook has one or two
structural defects a client would hit on first use. Ranked:

### Must fix before sharing

1. **The zip contains material its own text says must not be in it.** `BD_Owner_Only.docx` states
   "Not for the client share and not in the workspace zip" and is in the zip. `notes/pull_log_phase1.md`
   line 4 names the pursuing client: "WilcoSource / CitiusTech" (every client file says "Client").
   `notes/pass1.md` and the owner-only file describe the client's Drive folders and file titles.
   `notes/sense_check.md` names a data tool ("Sumble people search"), although the pull log's gate
   claims "re-scan of all files for data tool names: 0 hits". `data/teams.json` carries
   `credits_used: 150`. The build scripts hard-code `/home/claude/bd/ws/` paths.
2. **Summary, Context and history table: "Edwards Lifesciences Critical Care acquired Sep 2024 for
   $3.9 billion", graded Confirmed (S11, S41).** No captured source gives a price; the 10-K Item 1
   extract names the acquisition and the date only. Public reporting at the time put the deal at
   $4.2 billion. Re-check and regrade.
3. **Summary disagrees with itself on the Waters exclusion:** "We excluded 553 postings by their own
   wording" (How this could be wrong) versus 569 Waters positions everywhere else (section 5, the
   tracker, the Jobs workbook, `jobs_all.json`). 569 is right.
4. **People List row 568, Name = "Rashmi SAP Networker-rashmi_mala( at )yahoo.com"** (priority A,
   SAP lane): an e-mail address in a client file.
5. **Evidence file, Part I: seven sources have a placeholder URL `https://www.bd.com/`** (S42, S43,
   S44, S45, S46, S60, S61, the data pulls and the internal notes), and S41 = S48, S47 = S49. A
   reader following the link lands on BD's home page.
6. **Evidence file, Leads table (T10) row 6 cites J39** (the Veeva clinical systems posting) for
   the Director, Global CRM Technology tags; the right ID is J33. Row 11 (Partner for ServiceNow)
   has an empty Source cell, the only empty cell in the document.

### Should fix

7. **Job Postings: 1,041 of 1,897 rows (55 percent) have a blank Requisition**, because the build
   only reads "Job ID: R-" out of the description. The sheet has no J-ID column either, so the J
   numbers the Summary cites cannot be found in the workbook without the Evidence file. Three
   requisitions appear twice under different titles (R-553867, R-538437, R-547674).
8. **Job Postings: the "Open posting" link goes to LinkedIn for 129 rows and to Workday for 50**,
   while the sheet says every posting is from the BD careers site. LinkedIn job links expire.
9. **Job Postings: two BD rows are tagged Salesforce on a false match.** The keyword list includes
   "commerce cloud" and "experience cloud", which also match "SAP Commerce Cloud" and "Adobe
   Experience Cloud". R-549681 (Senior Manager, eCommerce Platforms, open) is counted as a
   **Salesforce build role**, so the Summary's "Salesforce build roles 11" is 10; R-545213 (Adobe
   AEM developer) is a Salesforce working tool row. A third such row is a Waters posting.
10. **People List: three same-person duplicate pairs** with the "Second profile" column empty in all
    736 rows: Fred Field (rows 48, 49), Bernadette Morales (329, 430, both priority A SAP) and Sunil
    Agarwal (27 VP of IT, priority A; 209 Director IT, priority B). The Summary's key-people table
    picked the Director IT row, not the VP. The Summary says duplicates were removed "by profile link
    and by name"; the build dedupes by link only.
11. **People List covers 9 of the 18 executives on the leadership page**, although the Summary says
    the list includes "people named in research (the leadership page, ...)". Missing: Goette (EMEA),
    Menziuso (Interventional), Minnix (General Counsel), Mocherla (Greater Asia), Muhsin (Connected
    Care), Silverman (CMO), Sorsher (Chief Quality Officer, the Veeva quality owner), Garrison
    (retired) and the announced Abuzaina. The 11 people added from research have no Location.
12. **Tech Stack and Teams, Platform teams: "First activity" is empty on all 25 rows** (the pull did
    not request it). "Waters Biosciences" (row 21) and "BD Diagnostic Solutions" (row 15) are listed
    as BD teams without the divested tag the Jobs workbook applies. "MDSAP" (a medical device audit
    program) is listed as an SAP lane technology on row 6 because the slug "sap" matched a substring.
    The sheet has 25 team rows; the header text says nothing about the count, but the Summary text
    says "the 25 largest".
13. **Tech Stack and Teams, Confirmed stack: Veeva has no row**, so the ranking shows "In confirmed
    stack: No" for Veeva while the Summary grades Veeva Confirmed in use (from vendor stories and
    postings). The sheet's "Postings naming it" counts (SAP 2,383, Salesforce 1,769) come from the
    stack pull's whole history, not the 12-month base, and the sheet does not say so. Several
    technologies repeat across categories (Databricks three times, MLflow three times), so 212 rows
    are not 212 technologies.
14. **Summary claims not found in the captured sources** (the helper read every captured page they
    cite): "approximately 8 percent gross productivity ... service levels above 90 percent" and
    "China down to about 4 percent" (attributed to the Q3 call, S6; not in the captured transcript);
    "Data and Marketing Cloud preferred" (J1; not on the captured page); "Roque named CFO" under S8
    (it is in S13); "dividend raised for the 54th year" under S10 (a separate release, not in the
    register); Arthur Hu's bio page (S3) was never captured. The two earnings-call transcripts (S6
    investing.com, S7 Motley Fool) are syndicated copies, yet call statements are graded Confirmed,
    which the grade table defines as "read in a primary source".
15. **Evidence file, news feed table (T14):** the first two rows are dated 2027-01-30 (a feed error
    for a January 2026 event) and 2026-12-31 (Fleming's retirement, a future date) in a table headed
    "newest first"; six more rows carry a feed date that contradicts the date in their own text; the
    Nebraska syringe investment appears three times, once as "$10M" against "$110M"; the Owen Mumford
    item the Summary says was dropped as Embecta's is still in the table; a redaction left
    "(name withheld), Inc."; and feed noise a client will notice remains ("Grasskeepers Landscaping
    signs new client", an Equal Opportunity boilerplate line, 2019 items, class action rows).
16. **Evidence file wording that reads like process:** "Page fetched twice (free reader and paid
    scraper)", "phase 1" (three rows), "36,655 employees per the tool", "Structured job feed, 8 pages",
    internal paths `data/releases` in Part G. The T11 heading says "lane platforms" and "out of
    scope platforms are left out" while the table holds Azure, AWS, Adobe, Coupa, Genesys and more;
    Agentforce still shows first seen 2020-09-01 with no note although Part F calls it a tagging error.
17. **Evidence file, Part B location labels contradict the posting URLs** for J29, J30, J33, J41,
    J42, J44, J52 (label "Franklin", slug le-pont-de-claix, san-diego, bengaluru, singapore,
    erembodegem), J34 and J50. Part B says each posting "was opened and read in full", but J9 is
    eight listings and J7 four listings of one role.

### Minor

18. House style: 5 People titles and the People Summary exclusion cell carry em or en dashes
    (`build_people.py` has no `clean()`); about 30 names carry credentials or an emoji ("PMP®",
    "MBA, CISSP, CISM ✨", "RT(R)(M)(CT)(BD)"); 7 titles have double spaces; three titles are
    divested-business roles (rows 107, 328, 369) without a mark.
19. People Summary: the security exclusion list has 29 entries but 28 names (Yvo Yrimia twice) and
    one non-Latin name; the grid tagged 32 security rows, the sheet says 29 (three left for other
    reasons, unexplained); "(8 people)" from the association searches is 6 on the sheet; the 4
    link-duplicates between the two title searches are why 1,484 - 120 - 1 - 29 - 605 = 729 and not
    725 + 11 = 736. Row 583 "Lead SAP GRC, Risk and Internal Control Specialist" is a compliance role
    kept as priority A.
20. Summary layout: 34 printed pages plus a blank page 35 (a trailing page break); pages 5, 12, 16,
    24 and 26 are mostly empty because every H1 forces a page break. Evidence: pages 7, 20, 34, 36,
    37, 55 and 60 are thin for the same reason; the T14 heading and two rows sit at the foot of page
    45 with the table on 46 to 54. Thousands separators are inconsistent in the Summary ("6552",
    "1659", "1068" against "1,068", "12,278"). Both Word files have an empty Title property and
    author "Un-named".
21. Job Postings: 149 rows are "Open" with a last-seen date before 19 October (feed semantics,
    unexplained); "What the posting says" is filled for 120 rows while the tracker says 52 were read
    in full; the Summary's "7,145 unique postings" is right (7,148 feed rows, 3 duplicate URLs).
22. Captured sources: 5 pages are saved twice under two names; 14 files have `date: unknown` and 5
    have an empty date line; 15 files carry the "[another program account]" redaction (correct).
    Apollo in the data is Apollo Tyres / Apollo Hospitals / a BD analytics product, not the tool.
23. Employee count: the Summary says "more than 60,000" (S4, pre-separation release) while S43 says
    36,655 "per the tool"; the sense check explains the drop but the Summary does not.
24. Michael Feld's CRO start is given as October 2025 (profile, S38) and February 2026 (CEO on the
    call, S6) in the same document.

## 3. What is right (so it is not re-checked)

- Jobs Summary: all 52 formulas recompute from the 1,659 BD rows (Salesforce 551/92, MuleSoft 10/3,
  ServiceNow 26/3, SAP 1,068/150, Tableau 43/2, Veeva 63/12, Workday 74/9; class splits identical).
  Entity split 1,655 BD + 4 Liberator + 200 Waters + 38 pre-close = 1,897. The ranking base 1,904 is
  the 33-platform dictionary; 1,659 is the 7-lane base; both are correctly described.
- People Summary: all 57 formulas recompute (736 people; 424 senior; A 80 / B 261 / C 83; 312 below
  the rule kept for a platform title). All 44 key-people names are on the sheet with matching lane
  and priority. Profile findings (413 profiles, median tenure 10, 209 ten-plus years, 398 posts
  pulled, 144 active, 41 of 80 A active) match `profile_findings.json`.
- Source register: S1 to S68 defined once, in order, every one cited in the Summary; J1 to J52
  defined once with links. 30 of 37 sampled quotes match their captured page word for word
  (figures, dates, names, segments, guidance, the Capgemini and PwC and ServiceNow stories).
- Tracker tally (Partial 4, Done 22, Blocked 2, None 3, Not done 3 over 34 rows) is right.
- Jobs links: 1,897 distinct targets, none broken in form. People links: 735 LinkedIn profile
  targets, none duplicated; only Anne Wu has none, and the sheet says so.
- Waters tagging: 540 postings carry the exact Waters line, 555 match the wider rule in the feed,
  569 after the live captures; pre-close divested 116; Liberator 10. The 120 non-bd.com employer rows
  (109 in Bangladesh) and the 32 security-tagged rows are real in `people_grid_rows.json`.

## 4. Notes for the product (parity work)

- The product's client-output denylist (`config/client_output_denylist.txt`, pattern
  `\d[\d,.]*\s+credits?`) fires 4 times on the BD Job Postings sheet on the text "1. Credit"
  (a numbered heading in a finance posting). The golden reference would be withheld by the gate.
- Defects of the reference that the rebuild should not copy: blank requisitions, no J-ID column,
  LinkedIn and Workday links labelled as the careers site, the empty First activity column,
  placeholder URLs for data pulls, the "(name withheld), Inc." artifact, feed dates in the future.
- The reference's "Second profile" column is the right place for same-person duplicates; the product
  should fill it or merge.
