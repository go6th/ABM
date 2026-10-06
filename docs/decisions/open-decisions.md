# Open decision register (project)

The base register D-01 to D-11 is in the skill (`references/12-open-decisions-and-verification.md`).
This file lists the project's extensions and later numbers. Nothing here is decided until the
product owner signs it; config blocks for proposals say `status: proposal`.

| ID | Decision | Default in use (proposal) | Where |
|---|---|---|---|
| D-01 ext | Product grade precedence; product grade of Conflicting (at most Probable) and Blocked (Hypothesis); judge-unavailable cap (engine C, Probable, `unjudged`) | as stated | ADR-0010, `config/grading_policy.yaml` |
| D-11 ext | Unknown-lineage group may count but not lift a claim to A (cap B, `needs_lineage_review`) | as stated | ADR-0012 |
| D-12 | Tracker completion policy | High rows must be Done or None for COMPLETED; pinned per run | ADR-0015, `config/tracker_v1.yaml` |
| D-13 | Tracker transition table, actor rules, outcome derivation, added reason codes (its `lead_proof` numbers are superseded by ADR-0013) | spec 5.2 and 5.3 with the additions in ADR-0015 | ADR-0015, `config/tracker_v1.yaml` |
| D-14 | Evidence dating (observed, published, unknown) and read origin per operation | as stated; p_search reads are `cached_copy` | ADR-0011 |
| D-15 | Source registry: tiers by domain and provider code, aggregator flags, lineage groups | unlisted = T4 flagged; brokers T2 TODO | ADR-0012, `config/source_registry.yaml` |
| D-16 | Posting class definitions and cues; full-text scope | rubric proposals; full text for build and admin_owner | ADR-0008, `config/posting_rubric.yaml` |
| D-17 | Lane keywords and platform aliases | platform names as board keywords; no aliases; Data and AI has no terms | ADR-0008, `config/job_lanes.yaml` |
| (new) | "Differently worded": metric, threshold 0.5, stopwords | Jaccard token set | ADR-0013 |
| (new) | Judge model profile distinct from the generator | none configured: every A/B/Confirmed capped | `config/models.yaml`, ADR-0010 |
| D-18 | Orchestration on Google Cloud | Cloud Tasks behind RunScheduler; named slice tasks; approval-timeout task | ADR-0018 |
| D-19 | LLM access path | Claude on Vertex AI by default, the Anthropic API as the alternative; model ids in config | ADR-0019 |
| D-20 | Projects, region and egress | one project per environment; asia-south1; direct VPC egress through Cloud NAT | ADR-0020 |
| D-21 | People scope definitions: level cues and bands, which band each seniority pass searches, priority A/B/C rubric, lane assignment, "top ten" order, the gap rule, search titles, profile levels | pass 1 Director and above plus Head, pass 2 Senior Manager; A = senior with a lane; gap = lane on with no in-scope person; top ten by priority, level, key; "<platform> <level>" titles until supplied; every in-scope person gets a profile | ADR-0021, ADR-0022, `config/people_rubric.yaml` |
| D-22 | People counts definitions: alumni, tenure buckets, certification matching | alumni = prior employer on the run's reference list, not computed without one; buckets under 1, 1 to 3, 3 to 5, 5+ years, precision kept; certifications matched to lanes by the lexicon | ADR-0023, `config/people_rubric.yaml` |
| D-23 | People data minimisation and erasure (D-06 pending counsel) | allow-listed business fields only; contact data dropped or redacted; tombstone erasure keeps key, status, canonical URL and merge chain, not exposed through the API | ADR-0024, ADR-0025, `config/people_rubric.yaml` |
| D-27 | Credit cap unit | one USD number per run (integer micro-dollars); credits per call in their own field; a cap in credits only with a sourced rate (none today) | ADR-0031, `config/spend.yaml` |
| D-28 | Beyond the cap | unattended: BUDGET_EXCEEDED at once; attended: at most one extension, strictly higher, freshly approved by a person; never automatic; within the cap no interrupt | ADR-0032 |
| D-29 | Breakdown and scope | breakdown advisory (variance reported), total the only hard limit; headroom 10%, step $0.05; LLM spend reported, not capped; tenant daily cap and refresh budget not built | ADR-0033 |
| D-30 | Spend visibility | spend report internal; tenant view of categories and call counts only, amounts once D-04 prices a credit; never in a client file | ADR-0034 |
| D-31 | Credit exhaustion | Blocked `credits_exhausted`, operator prompted, a registered pre-pulled file consumed at zero cost; fallback only when the tenant allows it (default false) | ADR-0035 |
| D-24 | Export content rules: banned-dash scope, stale phrases, abbreviations, partner framings, a restricted term inside a verbatim quote | authored prose only (em and en dash, spaced hyphen); stale and abbreviation lists empty (TODO); partner framings are the PRD's own words; a restricted quote is withheld, linked, counted and listed, never altered | ADR-0027, `config/export_rules.yaml` |
| D-25 | Workspace zip contents | RESTORE.md, MANIFEST.json, NOTES.md, the five deliverables and the render data (no builder code); raw broker payloads excluded until D-10; neutral-name copy built but off; organisation-provider payloads never | ADR-0028, `config/export_rules.yaml` |
| D-26 | Word renderer | the Node docx library as a sandboxed subprocess behind a JSON contract; an interim python-docx renderer in the same sandbox until the library is ported; production defaults to Node (NotConfigured until ported, so runs end PARTIAL) | ADR-0029, `src/exports/word/` |
| D-42 | Unit of work and merge | unit = (stage, lane, source type, shard) with a deterministic key; results merged by key with the state reducers; source codes allocated in key order (allocating bodies chained); unit outputs in domain tables and unit events | ADR-0037, `config/parallel.yaml`, `src/parallel/` |
| D-43 | Concurrency governor | Postgres leases with TTL and heartbeat through definer functions, per provider across all runs; run and tenant shares 50% (at least one slot), fewest-held tenant first, aged priority; AIMD from a floor of 1; every ceiling 1 until measured | ADR-0038, `src/parallel/governor.py`, migration 0017 |
| D-44 | The 4 hour target | simulation with stub providers (`docs/parallel/simulation-report.md`) and a staging injector protocol; real validation Blocked on real runs and credits (spike 4); C7 recorded | `docs/parallel/VALIDATION.md` |
| D-45 | Pause instead of fail (supersedes the D-28 default for unattended runs, in parallel mode) | one open request per cap version; at most 2 pauses; optional tenant ceiling (none set); approval after sign-in and a step-up younger than 10 minutes; decline or 24 h timeout ends BUDGET_EXCEEDED with partial results | ADR-0039, `src/spend/pause.py`, `config/spend.yaml` v2 |
| D-46 | Soft limit | 80% of the cap (proposal); notifies once per cap version and funds High rows and started units only | ADR-0039, `src/spend/limits.py` |
| D-47 | Exhaustion ladder (supersedes the D-31 default) | pre-pulled file, then an allowed alternate (tenant default true), then Blocked `credits_exhausted` with an exact upload spec; uploads operator only, quarantined, scanned (fail closed), strictly parsed, deduplicated, attested | ADR-0040, ADR-0041, `src/spend/exhaustion.py`, `src/spend/upload.py` |
