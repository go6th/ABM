---
name: "abm-account-research"
description: "Deep account research for ABM and outbound selling of enterprise platform services (Salesforce, ServiceNow, SAP, Tableau, Veeva, Data and AI). Use whenever Rajesh names a target account or list of accounts and wants signals, news, jobs, tech stack, platform owners, a buying group, an account brief, or a refresh of an earlier brief. Trigger on phrases like 'research Acme', 'run the ABM checklist on X', 'who owns Salesforce at X', 'what signals do we have on X', 'build the account summary for X', 'refresh the X brief', 'find the people at X', or any request pairing a company name with prospecting, account planning, signals or platform ownership. Also use it to update the ABM checklist or add a lesson. Produces the standing deliverables: graded Account Summary (Word), Sources and Evidence (Word), People List (Excel), Job Postings (Excel), Tech Stack and Teams (Excel) and the account workspace zip. No Executive Brief deck unless Rajesh asks for one. Client facing files never name data tools; they say 6th agents."
---

# ABM account research

This skill turns one company name into a sourced, graded account brief and a people list that a seller can act on. It exists because the first time this work was done by hand, the research was good but the process leaked: sources the user had asked for were skipped, findings were explained in chat and left out of the file, a table lost rows in rendering and nobody looked, and a wrong hypothesis survived because a lead was searched once with bad words and parked. Every rule below traces to one of those failures. Follow the process even when it feels slower, because the user's trust depends on never having to remind you twice.

## Read these first, every run

1. `references/checklist.md` is the living checklist. It holds the user's standing requirements, the run steps, the coverage rows that become the tracker, and the pre-delivery gate. Read it in full at the start of every run. It changes over time, so never work from memory of it.
2. `references/lessons.md` is the log of corrections the user has made. Skim it at the start of a run so old mistakes are not repeated.

Read the other references when you reach the step that needs them:

- `references/phase1-pull.md` and `references/phase2-build.md`: only for split mode runs.

- `references/signals.md`: the signal tiers, shelf lives and lane fingerprint questions. Read before Pass 2.
- `references/tools.md`: which tool to use for what, query patterns that work, costs and quirks, and the BitScale consent protocol. Read before the first tool call.
- `references/output-spec.md`: the exact structure of the three deliverables, the grading scheme, and the writing rules. Read before building any file.

## The shape of a run

1. **Set up.** Confirm the account name and domain. Ask once for the pursuing client if it is not known, because it decides whose references and history to check. If the user declines to say, continue and mark the relationship pass as blocked on that decision. Do not ask again in the same conversation.
2. **Pass 1, summary homework.** Relationship and fit questions. Cheap. Run on every account.
3. **Pass 2, detailed homework.** Signals by tier and lane, from every source type in the checklist. Build the people list first, then run person-name searches for speaking, podcasts and content.
4. **People.** Title search (BitScale) first, technology association (Sumble) only to fill gaps. Apply the seniority rule and the exclusions in the checklist.
5. **Distill.** Decide what changes a decision. Grade every line. Write hypotheses as hypotheses, each with a question that would confirm it.
6. **Build the standing files** (summary, evidence, people workbook, jobs workbook, tech stack and teams workbook, workspace zip) following `references/output-spec.md`.
7. **Run the pre-delivery gate** in the checklist, including the scripts. Fix what it finds before the user sees anything.
8. **Deliver once**, with a short note that says what was found, what was corrected, and what is still open. Anything said in chat about the account must already be in a file.

## Split mode (optional)

Split mode is the default from September 27, 2026: pulls on the lower cost model, analysis on the stronger one. The run splits at step 4. Phase 1 follows `references/phase1-pull.md`: every pull into files with a pull log, no interpretation, ending in `{Account}_phase1.zip`. Phase 2 follows `references/phase2-build.md`: starts from the raw files only, sends back at most one second pull list, then does steps 5 to 8. Phase 2 opens with a mandatory sense check (step 2a) that reviews entity boundaries, adjacent organizations, hidden titles and missed people in the raw pulls, and recommends targeted pulls for the user to approve. Both phases follow the same checklist, tools playbook and standing requirements. A single model run is used when the user asks for one.

## Rules that matter most

**Cover every row, visibly.** Each source type in the checklist ends the run as Done, Partial, Not done, Blocked or None, with proof: the queries run, the tool, and the result count. A row may be skipped, but only with a stated reason. Silent omission is the failure this skill exists to prevent.

**A keyword match is a lead, not a finding.** Read postings and articles before counting them. Keyword matches regularly include noise, such as "salesforces" meaning sales teams. Classify each item you read (core, working tool, tools list, false positive) and report the classification.

**A lead is not dead after one search.** Before marking anything unverified, try at least three differently worded queries across at least two tools, phrased the way a journalist would describe the event (company, city, plain nouns), not the way the snippet that raised it was worded. When later evidence supports a parked lead, search it again in the same run.

**Absence of evidence is weak evidence.** Never conclude a company does not use something because one job board had no mentions on one day. Check a second source type first, and grade what remains as Hypothesis.

**Grade everything.** Confirmed means read in a primary source this run. Probable means inferred from one or two sources with the reasoning stated. Hypothesis means your synthesis. Lead means seen in a secondary source and not verified. Only Confirmed items may be stated to a prospect. The rest become questions.

**Every fact carries its link in the same row.** No posting, article, person or captured listing appears without its URL. Career portal URLs come from the site itself, never constructed.

**Claims by threat actors, vendors and aggregators are claims.** Report who said it. Verify against an independent catalogue or the company itself where possible.

**Sensitive findings are preparation, not openers.** A breach, a layoff or a lawsuit is never an outreach hook. Say so in the file.

**Spend credits deliberately.** State the cost before any paid call the user has not already approved, prefer free paths (Parallel search and fetch, plain Firecrawl scrapes), and report credits used at the end. BitScale writes require its plan and consent steps, described in `references/tools.md`.

**Never delete or overwrite the user's data.** New grids, new files, new rows only. Ask before removing anything, including rows you added.

**A date pattern in a detection feed is a lead, not a finding.** "Technology X first seen 2022, last seen 2024" means the name appeared in public material for that period. It does not mean the platform was evaluated, adopted or dropped. Write what the dates show and grade it Lead or Hypothesis. The same holds for keyword tallies and alumni counts.

**When a profile is the evidence, say whose job it was.** A role description on a public profile counts only if it describes the person's work at the account, in their own words. Quote the sentence when the user asks. Never turn two profiles into a company direction.

**Plain English on every slide and heading.** No jargon a busy executive would have to decode. Say "the team keeps hiring developers every few months," not "built on a cadence." Spell out internal program names as internal names, and write out every abbreviation on first use.

**Client facing files never name the tools.** No vendor names for data or enrichment tools, and no credit costs, in any brief, evidence file, workbook or zip. Attribution, where needed, is "6th agents". The tools playbook in this skill is the only place they belong.

**Say what changed when re-sharing a file.** A re-shared file opens with one line on what is different from the last version. Never send a file again without that line.

**Every count names its base.** "118 of the 141 senior people" not "128 profiles". State whether a count includes duplicates, reposted requisitions or any point in a career.

## Refreshing an existing account

When the user asks for a refresh, start from the previous Summary's coverage tracker. Re-run High priority rows and anything whose signals have passed their shelf life, move expired signals to history instead of deleting them, and state at the top of the new Summary what changed since the last version. Reuse source IDs so the evidence file stays stable.

## Updating the checklist and lessons

The checklist is meant to evolve. When the user corrects the process, adds a source, changes the seniority rule, excludes a function, or approves a new tool:

1. Copy this skill folder to a writable location such as `/home/claude/abm-account-research/`, since the installed copy is read only.
2. Edit `references/checklist.md` (and `references/tools.md` or `references/signals.md` if relevant). Add a dated line to the change log at the bottom of the checklist.
3. Add a dated entry to `references/lessons.md` describing the mistake or request and the rule it produced.
4. Repackage with the skill-creator packaging script and present the `.skill` file so the user can save the new version. Keep the same skill name.
5. Tell the user in one or two sentences what changed.

Do this in the same conversation as the correction. A correction that lives only in chat is lost.

## Bundled files

- `scripts/rowcheck.py`: confirms every table row in a .docx appears in its rendered PDF. Catches rows dropped at page breaks.
- `scripts/doc_checks.py`: checks that every cited source ID has a link, flags banned dashes, and flags stale phrases and excluded names you pass in.
- `assets/docx_lib.js`: the docx-js helper for the account documents (tables with grade colors, links, headings, page setup). Reuse it so every account looks the same.
- `assets/people_workbook_example.py`: the merge, dedupe, level, lane and priority logic used for the people list. Adapt it per account.
- `assets/exec_deck_example.js`: used only when the user explicitly asks for a deck, which is not the default. An example pptxgenjs build of an executive brief, with the confirmed, guessing, hunch note structure, the financial chart and the challenges slide. Read the pptx skill before running it.
- `assets/jobs_workbook_example.py`: builds the Job Postings workbook from a 12 month pull of open and closed postings, with class, platform sentences, full description and link.
- `assets/strip_tool_names.py`: replaces data tool names with 6th agents across the build sources and checks the rendered files.
