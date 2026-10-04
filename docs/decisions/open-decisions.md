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
| D-24 | Export content rules: banned-dash scope, stale phrases, abbreviations, partner framings, a restricted term inside a verbatim quote | authored prose only (em and en dash, spaced hyphen); stale and abbreviation lists empty (TODO); partner framings are the PRD's own words; a restricted quote is withheld, linked, counted and listed, never altered | ADR-0027, `config/export_rules.yaml` |
| D-25 | Workspace zip contents | RESTORE.md, MANIFEST.json, NOTES.md, the five deliverables and the render data (no builder code); raw broker payloads excluded until D-10; neutral-name copy built but off; organisation-provider payloads never | ADR-0028, `config/export_rules.yaml` |
| D-26 | Word renderer | the Node docx library as a sandboxed subprocess behind a JSON contract; an interim python-docx renderer in the same sandbox until the library is ported; production defaults to Node (NotConfigured until ported, so runs end PARTIAL) | ADR-0029, `src/exports/word/` |
