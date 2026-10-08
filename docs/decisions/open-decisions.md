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
| D-48 | Model routing | task kind -> tier -> candidate ladder in config; fallback is selection on overload (after 2), model unavailable or context too long; never on schema, auth or content; judge independence by family; tenant data only to verified zero-retention candidates; router opt-in and refused without a benchmark | ADR-0042, `config/model_routing.yaml`, `src/llm/` |
| D-49 | Verification | blind source re-read of every Confirmed claim in distill; downgrade only via the G12 cap and `supersede`; figures in code; narrative by template | ADR-0043, `config/verify.yaml`, `src/verify/` |
| D-50 | Eval modes and statistics | replay every PR, live on model changes (not_run blocks), nightly; Wilson, seeded paired bootstrap, Holm at 0.05; k = 3; ARCH thresholds as initial values | ADR-0044, `config/evals.yaml`, `src/evalgate/` |
| D-51 | Routing governance | DECISIONS.md names each config hash with a benchmark or `proposal-unapplied`; checked in CI | ADR-0045, `docs/routing/DECISIONS.md` |
| D-56 | Telemetry backend and contract | OpenTelemetry to Cloud Monitoring, Trace and Logging; catalogs in `config/telemetry`; exporter not installed (V-OBS-1) | ADR-0046, `src/obs/` |
| D-57 | Cost derivation | rollups recomputed from the ledger, exact micro-dollars, reconciled; never a ledger | ADR-0047, `src/obs/costs.py` |
| D-58 | SLO windows and minimum samples | multiwindow burn 14.4x/1h+5m and 6x/6h+30m, 100 events minimum (proposal) | ADR-0048, `config/telemetry/slos.yaml` |
| D-59 | Paging policy and on-call tool | pages for availability burn, isolation and silence; tool and rotation are owner inputs | ADR-0049, `docs/oncall/` |
| D-60 | LLM tracing | span attributes and `llm_calls` only; no prompt text; no self-hosted tracer | ADR-0050 |

| D-61 | Tenant roles | admin, analyst, seller, viewer; analyst = seller plus shared views and tracker notes; no group mapping means no access; operator and support are staff roles, never tenant roles | ADR-0051, `config/roles.yaml` |
| D-62 | Identity architecture | in-app broker: OIDC (PyJWT) and SAML (signxml); tenant from the tenant's own IdP connection; server-side sessions; idle 30 min, absolute 8 h, step-up 10 min (proposals) | ADR-0052, `config/session.yaml` |
| D-63 | Cap and credits in the client app (C24) | depth tiers; allowance in units and percent; never credits, dollars or provider names | ADR-0053 |
| D-64 | White label | display name, logo, accent; attribution "6th agents" fixed; custom domains later | ADR-0054, `config/views.yaml` |
| D-65 | Connector set (C26) | Drive, Salesforce, HubSpot first; Gmail and call notes flagged off (`NotConfigured`); exact scopes; resolved ancestry; quarantine | ADR-0055, `config/connectors.yaml` |
| D-66 | Support impersonation | read-only view-as by a tenant-admin grant (reason, expiry, customer-visible); separate write grant; no break-glass; read 120 min, write 30 min (proposals) | ADR-0056, `config/support.yaml` |
| D-67 | Metering | five usage event kinds; active account = run or refresh in the period; six limit dimensions; pilot and standard values are proposals; atomic checks; units only | ADR-0057, `config/plans.yaml` |
| D-68 | Seller usefulness | two identifier-only event kinds; rates over 30 days with base and small-base label under 20 (proposals) | ADR-0058, `config/pilot.yaml` |
| D-69 | Audit of reads (C25) | resource-level, 60 s dedupe, identifiers only, hash chain, categories in the tenant export; retention TODO | ADR-0059, `config/audit.yaml` |
| D-70 | Broker keys | platform-held; write-only references; bring-your-own `NotConfigured` | ADR-0060 |
| D-71 | Shelf life per signal type | cadence from SaaS 5.1; shelf life = cadence period (7, 30, 91 days); null never scheduled; tenants may only lengthen (1x to 4x) | ADR-0061, `config/shelf_life.yaml` |
| D-72 | Probe and escalation | integer delta score; escalate at score 10 or brief age 90 days; at most one a week, two a month; escalations inherit the probe's pulls | ADR-0062, `config/refresh.yaml` |
| D-73 | Dedupe and scoring | fingerprint of type, entity, attributes, 7-day bucket; Jaccard 0.8 same, 0.5 ambiguous (linked, never merged); integer explainable score | ADR-0063, `config/scoring.yaml` |
| D-74 | Alert defaults | the ten rules plus facility expansion and vendor customer story (proposal); digest-only start, subscribe up; caps 1 news per account a day, 3 per user an hour, 10 a day, batch above 5 | ADR-0064, `config/alerts_extra.yaml` |
| D-75 | Leads | a match is a Lead until read; immediate needs read and verified at the rule's grade; unverifiable dropped and counted | ADR-0065 |
| D-76 | Sensitive findings | breach, layoff, lawsuit: fixed preparation templates, digest only, neutral subject, outreach lint everywhere | ADR-0066 |
| D-77 | CRM write-back (C28) | create-only client; enriched fields as new snapshot records, never existing fields (writing into empty fields: owner decision, default no); opt-in, mapped, previewed, approved | ADR-0067, `config/crm.yaml` |
| D-78 | Open Door | versioned schemas, read API, signed webhooks, additive-only in v1; no consumers or sending | ADR-0068, `docs/opendoor/` |
| D-79 | Monitoring budget | monthly allowance in plan units (pilot 2000, standard 10000), percent-only view, 80% notice once, deterministic ladder, hard stop, no rollover | ADR-0069, `config/budgets.yaml` |
| D-80 | Hardening scope | hermetic restore drill, kill-recovery test, report-only load profile, status page generator; real Cloud SQL restore, failover and DR Blocked | ADR-0070 |
| D-95 | ABM agent roster (proposal) | one agent per model step; pull, build, gate and delivery are code; pull output has no interpretive field | ADR-0092, `config/agents.yaml` |
| D-96 | Sense check and approval (proposal) | mandatory node before build; proposals priced in code; run within the approved cap, else pause (attended) or record with rows Partial (unattended) | ADR-0094 |
| D-97 | People data consent (proposal; **owner must approve the consent text**) | tenant-level insert-only record before any people pull; none valid until the archive's text is approved | ADR-0095, migration 0041 |
| D-98 | Pass 1 internal history (proposal; **owner decision**) | off by default; only with admin consent, named folders, connected connector; private content | ADR-0096 |
| D-99 | Lessons as code (proposal) | lessons.yaml entry, guard and test per lesson; check fails without them, INCOMPLETE without the archive | ADR-0097 |
| D-100 | MuleSoft lane naming (proposal) | named in the agent lane config and prompt; client lane label unchanged pending owner (C30) | ADR-0098 |
| D-101 | Skill versioning (proposal) | version and change log in config, run pin, repository updates only | ADR-0099 |
| D-102 | Run modes (proposal) | `full` default, `pull`, `build` by typed field only; slices of one graph; build pinned to its pull | ADR-0091 |

D-91 to D-94 are not recorded in this repository (the ABM prompt says earlier prompts used them).
