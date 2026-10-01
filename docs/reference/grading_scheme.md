# Grading scheme (client-facing labels)

The platform computes a numeric confidence (noisy-OR over lineage groups with recency
decay — Architecture Spec 6.2) and maps it to the four client-facing labels from the PRD.
The mapping and the writing rules below are normative (REQ-GRD-*, REQ-WRT-*, I8).

| Label | Meaning | Numeric rule (Spec mapping) | May be stated to a prospect? |
|-------|---------|------------------------------|------------------------------|
| **Confirmed** | Verified by at least two independent lineage groups, or one primary source (company site, filing, official release); fresh for its attribute class. | confidence ≥ 0.85 with ≥ 2 independent lineage groups, at least one T1/T2 (grade A) | **Yes — the only grade that may be stated to a prospect (I8).** |
| **Probable** | Single strong source or multiple weaker corroborations; consistent with the company's public footprint. | confidence ≥ 0.70 (grade B) | No — internal use; phrase as "appears to be". |
| **Hypothesis** | Weak, thin, or stale evidence; plausible but unverified. Includes conflicts between sources. | confidence ≥ 0.50 (grade C) or Conflicting | No — internal reasoning aid only. |
| **Lead** | Signal that research should continue: blocked sources, missing attributes, partial matches, or delta hints. | confidence < 0.50 (grade D), Blocked, or gap rows | No — never shown to a prospect. |

## Rules

1. **Prospect-safe rule (I8).** Only `Confirmed` may be stated to a prospect. The gate
   service (T67) blocks any client-facing artefact containing a `Probable`/`Hypothesis`/
   `Lead` fact phrased as fact, and rewrites or demotes it.
2. **Never silently omit.** Blocked, Unverified (Lead) and Conflicting facts appear in the
   deliverable's gap section with a reason (FR-06); they are labelled, never hidden.
3. **Recency decay.** A fact older than its attribute half-life (hiring 21 d, tech 90 d,
   leadership 180 d, funding 365 d) cannot be Confirmed until re-verified.
4. **Lineage groups.** Two brokers sharing one upstream count as ONE source (Spec 6.2);
   within a lineage group take the max weight, never the sum.
5. **Calibration.** The mapping is tuned against the golden set: expected calibration
   error ≤ 0.05, grade-A precision ≥ 0.95 (Spec 10.4).
