# T9 — Golden-set protocol (v0)

This document is the normative protocol for building and re-verifying the golden set used to
calibrate grade mapping and judge prompt/extractor changes (Spec 10.4; REQ-GRD-*).

## Purpose

1. **Calibration.** Tune the numeric-confidence → label mapping (grading_scheme.md) so that
   expected calibration error ≤ 0.05 and grade-A (Confirmed) precision ≥ 0.95.
2. **Regression safety.** Any change to prompts, extractors, source mix, or merge logic must
   be evaluated against the golden set before shipping; a drop in agreement below 0.80
   blocks the change.

## Account selection (9 accounts)

Three sectors × three profiles, chosen so every coverage row (C01–C33) is exercised at
least once across the set:

| Slot | Sector | Profile | Why it is in the set |
|------|--------|---------|----------------------|
| G-1 | SaaS / DevTools | VC-funded, ~200 staff, visible engineering hiring | funding + hiring + tech rows |
| G-2 | SaaS / DevTools | Bootstrapped, public pricing page | official-site T1 sources; thin broker coverage |
| G-3 | SaaS / DevTools | Recently IPO'd | filings-backed Confirmed facts; recency decay |
| G-4 | Fintech | Regulated, leadership churn in last 90 d | news + leadership_change + conflict rows |
| G-5 | Fintech | Acquisition announced in last 180 d | funding_event + delta rows |
| G-6 | Fintech | Non-US HQ | domain/locale edge cases; C2/C3 ambiguity |
| G-7 | Logistics | Low digital footprint | Blocked/Lead gap rows; gap-section behaviour |
| G-8 | Logistics | Heavy job-board poster | seniority/classifier rows |
| G-9 | Logistics | Legacy tech stack, recent adoption hints | tech delta rows; probable-vs-confirmed split |

Selection rules:

- All nine must have a resolvable canonical domain and at least one primary source
  (official site or filing), so Confirmed is achievable somewhere in each account.
- No account may be an employee's, investor's, or prospect's company (avoids prior knowledge
  biasing labels).
- Anonymised identifiers (slot ids G-1..G-9) are used in every internal artefact; company
  names live only in the labelling workspace, never in client-facing fixtures.

## Labelling process

1. **Capture snapshot.** Freeze source documents (HTML, PDF, broker JSON) into the labelling
   workspace with retrieval timestamps. Labels must cite only the snapshot, never live pages.
2. **Two independent labellers.** Each labeller grades every coverage row per account using
   grading_scheme.md, recording: label, confidence estimate, sources used, and a one-line
   evidence note.
3. **Adjudication.** Rows with label disagreement go to a third reviewer (the tech lead).
   The adjudicated label becomes the gold label; the original disagreement is recorded in
   the row's `notes` field.
4. **Numeric back-fill.** For each Confirmed/Probable row, the labellers also record the
   numeric confidence the system *should* output, so calibration error is measurable.
5. **Freeze.** The labelled result is written as `fixtures/golden_v0/<slot>.json` validated
   against `schema.json`. The set is versioned; labels never change silently.

## Agreement metric

- Per row, agreement = 1.0 when both labellers' labels match exactly, else 0.
- Set agreement = mean over all rows. **Exit criterion: agreement ≥ 0.80** before the set is
  frozen (v0 requires ≥ 0.80; if below, protocol wording is at fault — sharpen definitions
  and re-label; do not relax the threshold).
- Adjudication notes must explain each 0-agreement row that survived to gold.

## Re-verification cadence

- **Quarterly.** Refresh snapshots, re-grade leadership/hiring/news rows (short half-lives),
  and confirm frozen rows still hold. Attribute half-lives per grading_scheme.md rule 3.
- **On trigger.** Re-verify immediately when: grading scheme changes; a broker changes
  upstream; an account has a major event (funding, M&A, leadership change).
- Each re-verification produces a new fixture version (`golden_v1`, …) — prior versions are
  kept so historical calibration runs stay reproducible.

## artefact hygiene (I7/I8)

Golden-set fixtures are internal calibration data. They must never be rendered into client
artefacts, and labeller notes may reference broker names internally (unlike client-facing
briefs, which strip them per I7).
