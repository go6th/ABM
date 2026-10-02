# Open decision register (project)

The base register D-01 to D-11 is in the skill (`references/12-open-decisions-and-verification.md`).
This file lists the project's extensions and later numbers. Nothing here is decided until the
product owner signs it; config blocks for proposals say `status: proposal`.

| ID | Decision | Default in use (proposal) | Where |
|---|---|---|---|
| D-01 ext | Product grade precedence; product grade of Conflicting (at most Probable) and Blocked (Hypothesis); judge-unavailable cap (engine C, Probable, `unjudged`) | as stated | ADR-0010, `config/grading_policy.yaml` |
| D-11 ext | Unknown-lineage group may count but not lift a claim to A (cap B, `needs_lineage_review`) | as stated | ADR-0012 |
| D-12 | Tracker completion policy | see tracker work | `config/tracker_v1.yaml` |
| D-13 | Tracker transition rules (its `lead_proof` numbers are superseded by ADR-0013) | see tracker work | `config/tracker_v1.yaml` |
| D-14 | Evidence dating (observed, published, unknown) and read origin per operation | as stated; p_search reads are `cached_copy` | ADR-0011 |
| D-15 | Source registry: tiers by domain and provider code, aggregator flags, lineage groups | unlisted = T4 flagged; brokers T2 TODO | ADR-0012, `config/source_registry.yaml` |
| D-16, D-17 | Job pipeline (classification scope; lane keywords) | see jobs work | ADR-0008 |
| (new) | "Differently worded": metric, threshold 0.5, stopwords | Jaccard token set | ADR-0013 |
| (new) | Judge model profile distinct from the generator | none configured: every A/B/Confirmed capped | `config/models.yaml`, ADR-0010 |
