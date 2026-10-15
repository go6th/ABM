# ABM Account Research Skill: a reader's guide (version 2)

Converted from `ABM_Skill_Readers_Guide_v2.docx` (operator supplied 2026-10-16); the source
document stays in `docs/sources/` (not committed). The skill text it describes is in
`config/skill/archive/` (version 1.11, operator supplied 2026-10-16).

## ABM Account Research Skill

A reader's guide to version 1.8 and what changes for the SaaS version

This version adds source references for every statement and a Sources table at the end. It also corrects the MuleSoft note after rechecking the checklist.

## How to read this guide

Every statement in this guide comes from the skill file itself, identified by file name and line number in the Sources table. No third party or web sources were used. Statements marked Assessment in the Sources table are interpretations, not text found in the skill.

## What the file is

The skill file is a zip archive under a different extension. It holds 16 files in one folder called abm-account-research, organized into three subfolders. [S1] To read it without Claude, copy the file, rename the copy from .skill to .zip, extract it, and open the files in a text editor such as VS Code or Obsidian. [S2]

## The map

SKILL.md is the front door, at about 11 KB. [S1] It sets out the eight steps of a run: set up, summary homework, detailed homework, people, distill, build files, the pre-delivery gate, and delivery. [S3] It explains split mode, where pulls run on the lower cost model and analysis runs on the stronger one. [S4] It also lists the rules that matter most. Among them are covering every source row visibly, treating a keyword match as a lead rather than a finding, trying three phrasings before dropping a lead, grading everything, carrying a link with every fact, and never naming the data tools in client files. [S5] If only one file is read, it should be this one. [S15]

The references folder holds the detailed instructions. Claude opens each one only when a step calls for it. [S6]

checklist.md is the largest file at 23 KB. [S1] It holds the standing requirements, the coverage rows that become the tracker, the pre-delivery gate, and a dated change log running from version 1.0 on September 20 to version 1.8 on September 29. [S7]

lessons.md is the log of corrections made so far, written as general rules. [S8]

phase1-pull.md and phase2-build.md are the split mode runbooks. Phase 1 pulls everything into files and never interprets. [S9] Phase 2 starts with a mandatory sense check covering entity boundaries, adjacent organizations, hidden titles and contradictory facts before it builds anything. [S10]

tools.md covers which data tool to use for what, the query patterns that worked, and costs. signals.md covers the signal tiers and lane questions. output-spec.md gives the structure of each deliverable, the grading scheme, and the rules for the Owner Only file. [S11]

The assets folder holds templates and build code. It contains the Word document helper, the people and jobs workbook builders, the tool name stripper, and the executive deck example, which is used only when a deck is requested. [S12]

The scripts folder holds the automated checks. One confirms that no table rows were dropped at page breaks. One checks that every cited source has a link and flags banned dashes. [S12] One runs the third party domain scan. [S1]

## Suggested reading order

SKILL.md

references/checklist.md

references/phase1-pull.md and references/phase2-build.md

The remaining reference files, as needed [S15]

## One item to tidy

The description at the top of SKILL.md lists the lanes as Salesforce, ServiceNow, SAP, Tableau, Veeva, and Data and AI, and does not name MuleSoft. [S13] The checklist counts MuleSoft under the Salesforce lane and searches it on every account, so coverage is not affected. [S14] Naming MuleSoft in the description at the next update would make the summary match the file name and the checklist. [S15]

## Changes needed for the SaaS version

The skill was written for the owner working in the loop, and it says so in several places:

The description triggers on the owner naming a target account. [S13]

Setup asks for the pursuing client and confirms the credit cap for the run. [S16]

The checklist requires asking before spending credits that were not already approved. [S17]

Phase 2's sense check recommends targeted pulls that run once approved. [S18]

BitScale requires a consent flow before people pulls. [S19]

Phase 1 searches mail, call notes and named Drive folders in Pass 1. [S20]

The output spec creates a Owner Only file from internal sources. [S11]

Each of these becomes a per client setting, an approval step in the app, or a feature that is turned off for clients. [S15]

The Pass 1 internal history step needs the most thought. In a multi client service it would search the client's own mail and files, which raises access and privacy questions that should be decided deliberately rather than inherited from the current skill. [S15]

The section on updating the checklist tells Claude to copy the skill to a working folder and repackage it with the skill creator script inside the chat. [S21] In the SaaS, skill updates would go through the GitHub repository instead. [S15]

Everything else carries over largely as it is: the rules, the pre-delivery gate, the scripts and the output spec. That is the part that encodes the quality bar, and it is in good shape. [S15]

## Sources

All locations refer to the uploaded file Three_step_Opus_Fable_account_research_with_veeva_and_Mulesoft.skill. Line numbers are as extracted from that file.


| ID | Statement in this guide | Location in the skill file |
|---|---|---|
| S1 | File count, folder, subfolders, file sizes, presence of third_party_scan.py | Archive manifest of the .skill file (16 files; SKILL.md 11,402 bytes; checklist.md 23,326 bytes; scripts/third_party_scan.py) |
| S2 | The .skill file is a zip archive | File inspection of the uploaded .skill (identified as a zip archive). Opening steps are general guidance. |
| S3 | Eight steps of a run | SKILL.md, lines 23 to 32, The shape of a run |
| S4 | Split mode | SKILL.md, lines 34 to 36, Split mode |
| S5 | Rules that matter most | SKILL.md, lines 38 to 70, Rules that matter most |
| S6 | References are opened at the step that needs them | SKILL.md, lines 10 to 21, Read these first, every run |
| S7 | Checklist contents and change log dates | checklist.md, sections 1 to 7; change log lines 158 (version 1.8) and 172 (version 1.0) |
| S8 | Lessons log | SKILL.md, line 13; lessons.md, dated sections from 2026-09-20 to 2026-09-29 |
| S9 | Phase 1 never interprets | phase1-pull.md, line 7, Purpose, and the Phase 1 does not section |
| S10 | Phase 2 sense check | phase2-build.md, lines 10 to 21, Step 2a, the sense check |
| S11 | tools.md, signals.md, output-spec.md contents; Owner Only file | SKILL.md, lines 19 to 21; output-spec.md, line 66, Owner Only file |
| S12 | Assets and the row and document check scripts | SKILL.md, lines 88 to 96, Bundled files |
| S13 | Description lane list and the owner trigger | SKILL.md, line 3, description |
| S14 | MuleSoft counted under Salesforce and searched on every account | checklist.md, line 52, Scope defaults |
| S15 | Assessment | Interpretation by Claude, not text found in the skill |
| S16 | Setup asks for the pursuing client and confirms the credit cap | SKILL.md, line 25; phase1-pull.md, line 23, step 0 |
| S17 | Ask before spending unapproved credits | checklist.md, line 33, requirement 14 |
| S18 | Sense check pulls run once approved | phase2-build.md, lines 21 to 23 |
| S19 | BitScale consent flow | tools.md, line 66; phase1-pull.md, line 29 |
| S20 | Pass 1 searches mail, call notes and named Drive folders | phase1-pull.md, line 24 |
| S21 | Updating the checklist and repackaging | SKILL.md, lines 76 to 86, Updating the checklist and lessons |
