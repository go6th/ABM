# Conflict register (project additions)

The settled defaults C1 to C10 are in the skill (`references/00-sources-and-conflicts.md`).

| ID | Conflict | Resolution | Where |
|---|---|---|---|
| C11 | ARCH v2 targets AWS (Aurora, S3, Secrets Manager, Inngest); the owner instructed Google Cloud | Owner instruction wins; the cloud is behind ports, isolation and durability rules unchanged | ADR-0017 |
| C12 | PRD step 1 checks balances and resolves the careers site during setup; the careers lookup is a paid call and K2 forbids paid calls before an approved cap | The cap is proposed first; the careers lookup runs in setup only when the tenant policy approved the cap, otherwise first thing after the approval | `workflow/stages/core.py`, `stages/spend.py` |
| C13 | PRD: one credit cap; ARCH: a $5.00 hard broker cap (D-27) | Proposal: USD in micro-dollars, credits per call apart, $5.00 shown as the default | ADR-0031 |
| C14 | PRD: ask before spending beyond the cap; ARCH: BUDGET_EXCEEDED at the budget (D-28) | Proposal: attended runs may request one approved extension; unattended runs stop | ADR-0032 |
| C15 | PRD and SaaS: credits used in the delivery note; skill rule 6: no credit count in client output (D-30) | Proposal: the report is internal; the tenant view shows categories and call counts only | ADR-0034 |
| C16 | PRD: a pre-pulled file rather than a weaker source; `routing.yaml`: fallbacks on exhaustion (D-31) | Proposal: no fallback unless the tenant allows it; pre-pulled files consumed at zero cost | ADR-0035 |
| C17 | ARCH: per-broker concurrency via a Redis token bucket; GCP decision (C11): no Redis for coordination | Proposal D-43: Postgres leases through definer functions; Redis stays the pacing store only | ADR-0038 |
| C18 | D-28 (unattended runs end BUDGET_EXCEEDED at the cap) and the roadmap (pause for approval instead of failing) | Proposal D-45: the parallel mode pauses; sequential v1 keeps D-28 until the default mode changes | ADR-0039 |
| C19 | Work package text: a unit whose dependency failed is "Blocked dependency_failed"; tracker T1/T2: Blocked needs the row's own failed call as proof | The tracker's rule wins (one implementation per rule): such rows end Not done `dependency_failed`, and the failed dependency is recorded in the unit events | `src/account_intelligence/workflow/parallel_phase.py` |
