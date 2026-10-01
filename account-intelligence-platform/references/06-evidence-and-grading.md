# 06 Evidence and grading (ARCH 6.2, 6.3; PRD 3.4 and Appendix B)

Read this before writing `grading.py`, `normalise.py`, the `distill_grade` node or any code that decides what a client may be told.

## Two vocabularies, both kept (conflict C2)
| Layer | Values | Who it is for | Where defined |
| --- | --- | --- | --- |
| Product grade | Confirmed, Probable, Hypothesis, Lead (+ Internal in Phase B) | Sellers and clients: what may be said to a prospect | PRD Appendix B, SAAS 7.3 |
| Engine grade | A, B, C, D, plus Conflicting and Blocked | Computation, calibration, evals, alerts | ARCH 6.2 |

Store **both** on every fact (`product_grade`, `engine_grade`, `engine_confidence`) plus `read_in_primary_this_run`. The mapping between them is **open decision D-01**. `assets/grading.yaml` holds a *proposal* (Confirmed needs a primary-source read this run; engine A or B without that read is Probable; engine C or a synthesis is Hypothesis; engine D or aggregator-only is Lead). **Do not ship the proposal as settled; ask the product owner, then record the answer in the build log.**

## Product-grade rules (PRD) the code must enforce
- **Confirmed** = read in a primary source this run. Only Confirmed may be stated to a prospect.
- **Probable** (one or two sources, reasoning stated) and **Hypothesis** (the system's synthesis) become questions, never statements. Every Hypothesis carries a confirming question.
- **Lead** = seen in a secondary source, not verified; aggregator sources are labelled. A Lead stays a Lead only after **three differently worded queries across at least two tool categories** (`tool_categories` in `assets/routing.yaml`). The coverage row stores those queries as proof.
- **Absence of evidence is graded Hypothesis**, never a conclusion. `grade_attribute()` returns `no_evidence`; the caller records the Hypothesis.
- A keyword match is a lead. A date range in a detection feed is a lead.
- **Internal** (Phase B): candidate fact from client internal material. Never Confirmed from internal alone unless the client marks the source authoritative. A grade rises only when a public source or an authoritative internal source supports it and the reasoning says which.

## Engine rules (ARCH 6.2)
- Source tiers and weights (starting values, tune on the golden set): **T1 primary 0.90** (company site, filings, official releases) · **T2 curated structured brokers 0.75** · **T3 reputable news 0.70** · **T4 scraped or aggregated 0.40**.
- Recency decay `r = 0.5 ** (age / half_life)`. Half-lives: hiring 21 days, technology 90, leadership 180, funding 365.
- **Lineage groups.** Two sources deriving from the same upstream are one independent source. Within a group take the **maximum** of `w * r`, never the sum.
- Confidence (noisy-OR) `c = 1 - prod over lineage groups of (1 - w_i * r_i)`.
- Grades: **A** `c >= 0.85`, at least two independent lineage groups, one of them T1 or T2 (display Verified) · **B** `c >= 0.70` (Likely) · **C** `c >= 0.50` (Indicative) · **D** `c < 0.50` (Unverified, shown and clearly labelled).
- **Conflicting**: two independent groups assert different normalised values, both with `c > 0.50`. Show both values with evidence.
- **Blocked**: a required source failed or was rate limited after retries. Show the reason; never omit.

Worked examples (they are unit tests in `scripts/grading_reference.py`):
- Fresh T1 (0.90) and fresh T2 (0.75), independent: `c = 1 - 0.10 * 0.25 = 0.975`, grade A.
- Two aggregators copying one upstream: one group, `c = 0.40`, grade D.
- One fresh T1 source: `c = 0.90` but only one group, so grade B, never A.
- Two fresh T3 groups: `c = 0.91` but no T1 or T2 group, so grade B.

## How to implement
1. Copy the logic of `scripts/grading_reference.py` into `src/agents/grading.py`. Keep it **pure** (no I/O, no model calls). Load weights and half-lives from config, not constants.
2. Property tests (hypothesis): confidence is in [0, 1]; adding an independent source never lowers `c`; adding a source to an existing lineage group never raises `c` above that group's maximum contribution; grade is monotone in `c`; decay is monotone in age. **100% coverage on grading and billing** (ARCH 15).
3. **Normalise in code, not in the model**: case, titles, currency, dates, domain forms. `fingerprint = hash(entity, attribute_key, normalised value)`. Equal facts must collide so deltas are exact. Example key: `exec.cto.name`.
4. **Lineage groups need a source registry**: a table or config mapping domains and broker datasets to an upstream group (e.g. two brokers that both republish one professional network). Unknown provenance defaults to its own group **but is flagged** so the AI engineer can review; do not guess that two sources are independent.
5. **No fact without at least one `fact_evidence` row.** The `distill_grade` node drops claims without evidence; the DB enforces it with a deferred constraint or a write path that inserts fact and evidence in one transaction.
6. **Verify node**: judge model differs from the generator; it checks every grade-A/B or Confirmed claim against the cited span; failed claims are downgraded or dropped, and the downgrade is logged.
7. **Calibration**: on the golden set plot predicted confidence against observed correctness; track expected calibration error (gate <= 0.05). "A is right 95% of the time" is a product claim; measure it before saying it.

## Coverage rows (33, from the PRD tracker)
`assets/checklist.yaml` seeds `coverage_rows` for every run: 5 are Pass 1 and 28 are Pass 2; 19 High, 7 Medium, 7 Optional. A run cannot reach COMPLETED while a High row lacks a status. Allowed statuses: Done, Partial, Not done, Blocked, None. Each stores proof: the queries run, the tool category, the result count. The tracker tally is recomputed from rows, never typed.

## Counts always name their base
"14 postings read of 31 matched, 9 open and 5 closed" is acceptable; "14 postings" is not. Store `read`, `matched`, `open`, `closed` as separate integers and render the sentence from them.
