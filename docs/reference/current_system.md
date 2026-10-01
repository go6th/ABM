# Current system — how the Claude skill works today (pre-platform)

The proven manual capability (skill `abm-account-research` v1.5) that the platform must
reproduce as automated behaviour. Nine graded account briefs were delivered in
September 2026; nine delivered file sets are archived in the ABM Research Drive folder.

## Skill folder layout

```
abm-account-research/
├── SKILL.md                  # entry point: triggers, run procedure, output contract
├── checklist.md              # living checklist (33 coverage rows with proof rules)
├── lessons.md                # lessons log; updated after every run
├── signals.md                # signals reference: what counts as a buying signal
├── tools-playbook.md         # broker routing: which tool per attribute, credit habits
├── output-spec.md            # the seven-file deliverable contract
├── scripts/
│   ├── rowcheck.py           # coverage-tracker row checker (proof present per row)
│   ├── doc_checks.py         # per-file deliverable checks (structure, labels)
│   ├── third_party_scan.py   # scans client files for broker/tool names (I6)
│   └── strip_tool_names.py   # rewrites tool mentions out of client-facing files (I6)
├── lib/
│   ├── docx_builder.py       # Word document builder (Account Summary etc.)
│   └── xlsx_builder.py       # Excel workbook builder (trackers, people, postings)
└── accounts/
    └── <domain>/             # one workspace per account (I7: never shared)
        ├── tracker.xlsx      # coverage tracker (33 rows, proof columns)
        ├── evidence/         # raw quotes with URLs and dates (lineage per row)
        └── out/              # the seven deliverable files + internal-only file
```

## The 13-step run

1. **Intake.** Fix the account: canonical domain from company name; create the account
   workspace; copy the tracker; state the one-cap-per-run credit budget.
2. **Baseline.** One cheap overview pass on the official site: identity rows
   (C01–C03), description, leadership page. Record quotes with URL and date.
3. **Plan branches.** From the tracker, list rows still uncovered; choose sources per
   the tools playbook (which broker for which attribute).
4. **Fan out.** Query news, jobs, people and tech sources; save raw payloads into
   `evidence/` with source URL and retrieval date.
5. **Extract.** For each source, lift candidate facts as quoted spans; note source tier
   (T1 official, T2 curated, T3 news, T4 scraped).
6. **Corroborate.** Group evidence into lineage groups; two aggregators with one
   upstream count once; drop duplicates by normalised value.
7. **Grade.** Apply the grading scheme: Confirmed / Probable / Hypothesis / Lead per
   row; never silently omit — Blocked rows get a reason.
8. **People.** Build the deduplicated people list from leadership sources and profiles;
   record tenor; apply exclusions; leads need 3 queries across 2 tools (PRD 3.4).
9. **Deliverables.** Render the seven-file set (Word + Excel via the builders):
   summary, tracker, evidence, people, postings, tech stack, sources; one workspace
   zip per account.
10. **Internal-only split.** Relationship history, contact data and any internal notes
    go to the Internal-Only file — never into client files (I7).
11. **Gate.** Run `rowcheck.py`, `doc_checks.py`, `third_party_scan.py`,
    `strip_tool_names.py`: every row has proof; no broker/tool names (I6); no internal
    leakage (I7); only Confirmed stated as fact (I8). Fix and loop until clean.
12. **Package.** Zip the client set; write the run log entry (credits spent, coverage,
    gaps) to the lessons log.
13. **Refresh.** On the next run for the same account: start from the previous tracker,
    re-use source IDs, and only re-fetch rows whose half-life expired (PRD 4.5).

## Known limitations of the manual system

- Single operator, no tenancy; workspace isolation is by convention (folder per
  account), not enforced.
- Credits are tracked by habit, not by a ledger; cost attribution is manual.
- Grading is applied by judgement, not by formula; calibration is unknown.
- Refresh is manual with no scheduler and no delta alerts.
