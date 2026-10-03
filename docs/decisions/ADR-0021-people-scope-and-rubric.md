# ADR-0021: People scope and rubric (D-21)

- Status: Proposed
- Date: 2026-10-03
- Deciders: product owner (TODO)

## Context

PRD s3.3 and s4.2 step 7 name the default scope (Director and above, Head, Senior Manager;
global; security, IT compliance and IT audit excluded), two seniority passes on the same source,
association search only to fill gaps, level, lane and priority, and "posts for priority A only".
They do not define the title-to-level mapping, the search titles, what separates the passes, how
a lane or a priority is assigned, the exclusion cues beyond the three names, "top ten" or "gap".
No model may decide who is in scope.

## Decision

Everything the sources leave open is configuration (`config/people_rubric.yaml`, `status:
proposal`), applied by pure code with a `rule_id` and the matching substring as evidence:

- Levels by whole-word cues; the most senior match wins (`level_order`). Seeded only from the PRD
  wording (director, head, senior manager), plus proposals for "and above" (vp, svp, evp, chief)
  and for manager (below scope). Abbreviations such as "Sr." are not seeded: they need the
  product owner, and until then such a title is scoped by its literal words.
- Bands: pass 1 searches Director and above plus Head (`senior`), pass 2 Senior Manager
  (`manager`).
- Exclusion cues are exactly the three PRD names; a title naming "compliance" or "audit" without
  "IT" goes to `review(exclusion_ambiguous)`. Exclusion beats inclusion.
- Lanes use the job pipeline's lexicon (one implementation): platform names and aliases in the
  title or headline.
- Priority: A = senior band with a lane; B = senior without a lane, or manager band with a lane;
  C = manager band without a lane.
- Status order: merged, not current, excluded, unresolved (no profile URL), employment unknown,
  level unknown or below scope, geography, ambiguous exclusion, priority unmatched, in scope.
- A record from a search restricted to the account domain counts as current unless the source
  says otherwise (`is_current` false or another employer domain).
- Gap: a lane that is on and has no in-scope person after the title search; at most one
  association search per gap lane per run.
- Top ten: priority, then level order, then the stable person key.
- Search titles: none supplied; until then each (lane, pass) searches "<platform> <level word>"
  (`title_fallback: platform_and_level`, set `none` to end those rows Not done).

## Consequences

The product owner must supply or confirm title lists, cues, bands, the priority rubric and the
gap rule. Every rule has a positive and a negative example in config, and a test fails if one
stops holding. `docs/people/rubric.md` is generated from the config.
