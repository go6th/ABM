# 12 Open decisions, verification register and risks

## Open decisions (ask the person; record the answer in `docs/decisions/` and BUILD_LOG.md)
| ID | Decision | Default until decided | Blocks |
| --- | --- | --- | --- |
| D-01 | Mapping between engine grades (A to D) and product grades (Confirmed, Probable, Hypothesis, Lead) | Keep both; Confirmed requires a primary-source read this run; mapping in `assets/grading.yaml` is a **proposal** | WP 1.6, 1.8 (what the files print) |
| D-02 | Model split per workflow step | Tiered routing by config; decide on measured gate-failure rate | WP 1.5 prompts and evals |
| D-03 | BitScale alone vs BitScale plus Sumble for people | Keep both behind the adapter; measure fill rate and cost per usable senior person over ten accounts | Spike 4 |
| D-04 | List price per run or credit; depth tiers; credits to USD rate | Unset (the starter code hard-codes 6 and 18 credits as illustrations) | WP 1.7, 2.2 |
| D-05 | Required data residency for the first ten customers; silo tier on day one? | Mumbai primary, Hyderabad standby from V1 | WP 1.1, 3.6 |
| D-06 | Which people-data attributes are in scope for the brief, given the privacy review | Business contact data only, pending counsel | People deliverables |
| D-07 | Is the operator console a long-term product surface? | Treat as a real surface in MVP | WP 1.9 |
| D-08 | First Phase B connectors | Grain first, then CRM and Drive | Phase 3 |
| D-09 | Commercial policy when two tenants pursue the same account | Isolation handles the data; a written position is needed before phase one | Go-to-market |
| D-10 | Broker terms on resale and derived data | Confirm before phase one | Spike 4, public cache |
| D-11 | How lineage groups are determined (which sources share an upstream) | Own group per source, **flagged for review**; the sources do not say how to decide | WP 1.6 |

D-01 to D-10 were collected from the three sources (ARCH 19.3 raises D-04 to D-08). **D-11 was added while writing this skill**; no source says how lineage is decided.

## Verification register (ARCH Appendix B). Resolve before the dependent decision is locked
| # | Verify | Why it matters | Blocks |
| --- | --- | --- | --- |
| V1 | Current model IDs, prices, cache lifetimes, multipliers, minimum cacheable length, breakpoint limit | Cost model and cache design | Spike 3, config |
| V2 | Aurora Global Database support for the chosen engine version in ap-south-2; measured inter-region latency | DR plan and RPO | WP 2.5 |
| V3 | pgvector version on Aurora; iterative index scans; halfvec limits | Filtered retrieval design | Phase 3, spike 1 vectors |
| V4 | Inngest step time and payload limits, concurrency-key syntax, failover and residency, Python SDK signatures | Slice design and starter code | Spike 2 |
| V5 | Zero-retention and no-training terms for LLM, embedding and speech vendors | Phase B privacy promise | Phase 3 |
| V6 | Speech recognition pricing and diarization quality on real call audio | Phase B economics | Phase 3 |
| V7 | Broker contract pricing, rate limits, permitted use of derived data | Unit economics and compliance | Spike 4 |
| V8 | Karpenter and spot savings on our workload | Compute cost | WP 1.1 |
| V9 | OWASP LLM Top 10 edition in force at adoption | Security mapping | Docs |
| V10 | DPDP Act, GDPR and similar applicability to people data in briefs | Launch readiness | Before first client |
| V11 | LangGraph checkpoint library version: `setup()` behaviour, schema, tables | Durability implementation | Spike 2 |

Record owner and date in BUILD_LOG.md when closing one. **The skill resolves a [VERIFY] by testing or reading first-party docs where it can, and says "verified by running X" or "not verified"; it never states a vendor fact from memory as settled.**

## Risk register (ARCH 19.1, abbreviated)
Cross-tenant leakage via RLS misconfiguration (low likelihood, severe: layered isolation, migration lint, isolation gate, canaries, external test) · broker cost exceeds price per run (high/high: per-run caps, probe-then-escalate, ledger-driven negotiation, depth pricing) · silent prompt-cache invalidation · indirect prompt injection from scraped or private content (high/high: quarantine, typed tools, adversarial corpus) · workflow engine limits or outage (slice design, reconciler, documented Temporal alternative) · filtered ANN under-recall for small tenants (partitions, iterative scans, per-tenant recall probes) · checkpoint and ledger growth (7-day post-terminal retention, offload large payloads) · privacy or licence challenge on people data and scraped sources (medium/severe: legal review before launch, source licences, objection handling) · golden-set decay (capture dates, quarterly re-verification, paired comparisons) · regional failover misses RTO (scripted runbook, game-day).
