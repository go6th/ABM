# Phase 2 runbook: analysis and deliverables (split mode)

Use this file when the user starts phase 2 from a phase 1 zip. Read `checklist.md`, `lessons.md`, `signals.md` and `output-spec.md` first.

## Start

1. Unzip the phase 1 package. Read `pull_log.md` and check it against the phase 1 sequence in `phase1-pull.md`. Any step missing, any lane keyword not searched, any page not reached is a gap.
2. Do not read any summary phase 1 wrote. Work from `data/` only.

## Step 2a, the sense check (mandatory, before any building)

Phase 1 pulls blindly by rule. This step is where the judgment that a single model run applies during the pulls is put back. Read the raw files as an analyst reading the ground for the first time, and answer these questions in writing before touching a deliverable:

1. **Entity boundaries.** Which records actually belong to the account? Look for a parent, a sibling entity, a shared services arm or a same named organization elsewhere (a health plan inside a health system, a New York plan with the same product name, a hospital supply chain team tagged to the plan). Check the location tags, the affiliate or entity labels in postings, the current employer on profiles, and the domain each record came from. List every row set that looks misattributed and say how to confirm.
2. **Adjacent organizations that matter.** Which neighbouring entity holds capabilities the account depends on (parent IT, a shared data platform, a group ERP program)? Say whether it should be pulled as a labelled adjacent section, and what one or two pulls would show it.
3. **Hidden titles and program names.** Which postings or profiles carry a lane platform behind a generic title, and which internal program names appear (Sightline, Modern Data Platform)? Which of these need a full read or a name search that phase 1 could not know to run?
4. **Stale or contradictory facts.** Where do two sources disagree (membership, revenue, a leader's title)? Which one needs the primary source opened?
5. **People the rule missed.** Which senior people are named in news, releases or posts but absent from the people pull? Which people on the pull look like they belong to another organization?
6. **What would change the brief.** For each candidate pull, say what finding it could produce and whether that finding would change a hypothesis, a priority or a lane picture. If it would not, drop it.

Write the answers as `notes/sense_check.md` with three lists: (a) corrections phase 2 will apply itself from the data already pulled, (b) targeted pulls recommended, each with the exact tool, query, expected output file, estimated credits and the decision it serves, ranked by what it changes, and (c) items deliberately not pursued and why. Present list (b) to the user as a short recommendation with a total credit estimate and ask for a yes. Not every account needs list (b); an empty list with reasons is a valid result and should not be padded.

Once approved: pulls that need judgment during the pull (reading a posting to decide, matching a person by hand, choosing among search phrasings) run inside phase 2. Bulk, mechanical pulls (another feed page, profiles for a list of URLs, a second board) go back to phase 1 as the second pull list, one round trip per account. Log every pull either way and reuse source IDs.

## Gaps and the second pull

- The second pull list comes out of the sense check above: exact tool, query and expected output file per item. Common ones: a posting whose title hides a lane platform and was not scraped in full, a former executive not searched, a careers site that moved, a sibling entity that needs its own pull.
- Aim for one round trip per account. If a gap can be closed with a free web search or page fetch inside phase 2, close it here and log it.

## Work

Steps 5 to 8 of the run, unchanged: read and classify every lane posting, apply people exclusions and priorities, compute profile findings and posts activity, grade every line, write the narrative, hypotheses and tracker (proof column from the pull log), build the standing files from `output-spec.md`, run the full pre-delivery gate and the third party source check, and deliver once with credits taken from the pull log.
