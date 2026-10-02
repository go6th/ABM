# ADR-0013: Lead rule ownership and "differently worded"

- Status: Proposed (similarity metric and threshold need the product owner)
- Date: 2026-10-02
- Deciders: TODO (product owner)

## Context
PRD 3.4: "at least three differently worded queries across at least two tools before anything is
marked unverified." The tracker had its own check (difflib ratio 0.8 on raw strings, which counted
failed calls' categories and caller-supplied text).

## Decision
* One implementation: `account_intelligence.grading.leads`. The tracker (`tracker/leads.py`) and the
  workflow verification stage call it; the tracker's `lead_proof` config is superseded.
* Numbers from `grading.yaml` (3 queries, 2 categories). A query counts only from a successful call
  in this run's ledger, using the ledger's category and only text the call actually sent.
* Proposal: token-set Jaccard after NFKC, lowercase, punctuation, stopword and account-token removal;
  accepted only if similarity < 0.5 against every accepted query. Reordering, case, punctuation or
  adding the account name do not make a query different.
* Bounded: at most 3 accepted, 3 rejected per pass (then `incomplete(no_distinct_query)`), 1 re-pass.
  A Lead whose verification is pending or incomplete is never displayed as a plain Lead.

## Consequences
Stricter than before: synonyms count as different words, paraphrases with the same words do not.
The threshold and stopword list need product-owner review against real queries.
