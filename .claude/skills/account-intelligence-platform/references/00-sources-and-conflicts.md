# 00 Sources, precedence and conflicts

## The three source documents

| Tag | Document | Use it for |
| --- | --- | --- |
| **PRD** | Account Intelligence: business use case, product requirements and how the current system works (6th, Sept 2026) | The research **process**, rules, coverage rows, deliverables, quality gate, grading language |
| **SAAS** | Account Intelligence as a Service: multi-client SaaS design, scheduled refresh and alerts, enhanced version (6th, Sept 2026) | **Product** shape: tenants, portal, refresh cadences, alert rules, credits and pricing, internal-material version, risks |
| **ARCH** | Account Intelligence SaaS: Architecture & Implementation Specification v2.0 (49 pages) | **How to build it**: isolation, durability, data model, agent graph, API, billing, LLMOps, infra, CI/CD, tests, cost, phases, starter code |

Note: the user supplied two copies of the PRD; the PDF and the SaaS docx are the other two.

## Precedence when documents disagree

1. **ARCH** wins for *how to build* (stack, schema, orchestration, tests, phases).
2. **PRD** wins for *what the research process must do* (steps, coverage rows, grading rules, gate, deliverables, constraints).
3. **SAAS** wins for *product behaviour* (what a client sees, cadences, alert tiers, pricing parts, two-output rule).
4. If two documents conflict on something that changes a build decision and the table below does not settle it, **stop and ask**; do not pick silently.

## Conflict register (settled defaults)

| # | Topic | Documents | Default to follow |
| --- | --- | --- | --- |
| C1 | Workflow engine | SAAS: Temporal or Inngest. ARCH: **LangGraph checkpoint is the durability source of truth; Inngest only triggers, schedules and loops slices**; Temporal is the documented alternative | ARCH. Move to Temporal only if Phase 0 spike 2 fails |
| C2 | Grade vocabulary | PRD/SAAS: Confirmed, Probable, Hypothesis, Lead (+ Internal in Phase B). ARCH: A to D by noisy-OR confidence, plus Conflicting and Blocked | Keep **both**. Engine computes A to D; product labels stay the client contract. Mapping is open decision **D-01** (proposal in `assets/grading.yaml`) |
| C3 | Tenancy timing | SAAS: full tenant model from day one, do not defer. ARCH: RLS and isolation gate in MVP | `tenant_id NOT NULL` + forced RLS in the **first migration**. Never ship a single-tenant schema "for now" |
| C4 | Hosting stack | SAAS: Vercel, WorkOS/Clerk, Supabase/Neon, R2. ARCH: AWS (Mumbai primary, Hyderabad standby), EKS, Aurora, S3+KMS | ARCH. Treat SAAS list as alternatives. Any OIDC IdP issuing `tenant_id` and `role` claims works |
| C5 | Phase naming and weeks | SAAS: phases 0 to 3. ARCH: 0 Prove (wk 1-3), 1 MVP (4-11), 2 V1 (12-19), 3 Scale/Phase B (20-28) | ARCH weeks; they describe one sequence |
| C6 | Internal-only file name | PRD names it after an operator; SAAS: "Internal Only" | "Internal Only". Never put a person's name in a file, role or product name |
| C7 | Run duration | PRD: about one working day today. ARCH: p50 < 10 min, p95 < 45 min targets | ARCH numbers are **targets to prove** in Phase 0 (spike 4 measures cost and coverage on 20 real accounts); "standard" depth may cover fewer sources than the PRD's full scope |
| C8 | Alert rules | SAAS 5.3 tiers; ARCH 6.4 noise controls | Merged in `assets/alert_rules.yaml` |
| C9 | Public cache | SAAS 3.3: optional, per-tenant setting, default off for enterprise. ARCH 16.4: share only where licences allow | Per-tenant setting, default **off**; public raw payloads only; never anything derived or private |
| C10 | Fact shape | PRD: narrative rows each with a stable source ID (S-0001...). ARCH: entity + attribute_key + normalised value + fingerprint | Both: structured fact rows (ARCH) **and** stable per-account source codes (PRD) used by every export |

## Items the sources mark as unverified

ARCH tags vendor facts it could not confirm as **[VERIFY]** and lists them in its Appendix B (V1 to V11): model IDs, prices, cache lifetimes and multipliers; Aurora Global support in ap-south-2; pgvector version and iterative scan; Inngest limits and SDK call shapes; vendor zero-retention terms; speech vendor pricing; broker contract terms; Karpenter savings; OWASP edition; DPDP/GDPR applicability; LangGraph checkpointer behaviour. **Resolve the relevant item before locking the decision that depends on it, and say so in the build log.** The ARCH starter code was compile-checked, not run against live services.

## Internal names

"6th" is the company. Client-facing material attributes work to "6th agents". Broker names (Parallel, Firecrawl, PredictLeads, BitScale, Sumble) are **internal** and appear to a client only in the sub-processor list of the data processing agreement.
