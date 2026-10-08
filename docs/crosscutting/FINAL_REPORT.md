## Conformance Audit

**Before this package:**
- X1 (Data model): **Done**
- X2 (Connector contracts): **Done**
- X3 (Quality system): **Done**
- X4 (People data & retention): **Partial** (`test_retention_removes_exactly_the_dry_run` missing)
- X5 (Security & assurance): **Missing**
- X6 (Unit costs & anomaly alerts): **Missing**
- X7 (Success metrics): **Missing**
- X8 (Registers & release): **Missing**

**After this package:**
All requirements are now **Done**. See `docs/crosscutting/CONFORMANCE.md` for the exact proofs (columns, constraints, RLS policies, symbols, and tests mapped to every requirement id).

## Workstreams

| Workstream | Status | Components |
|---|---|---|
| **X1 Data Model (11.1)** | Done | Executable spec, DM1-DM7 constraints, careers URL visited, anchored facts, merged people, export verifier. |
| **X2 Contracts (11.2)** | Done | Typed ops, retry, rate limit, cost per call, test double, contract drift alerts (CX1-CX3). |
| **X3 Quality (11.3)** | Done | Checklist as code, three-layer release criteria, regression CI, analyst loop (QS1-QS3). |
| **X4 Privacy (11.4)** | Done | Minimisation map, retention dry-run/execute jobs, tombstones, DSAR workflow, suppression list, counsel templates. |
| **X5 Security (11.4)** | Done | Isolation (RLS), secrets scan, rotation schedule, audit chain, untrusted content handling, SOC2 matrix, pentest gate, tool/prompt logs (D-81). |
| **X6 Unit Costs (11.5)** | Done | Rollups for broker credits, tokens, infra allocation, analyst review minutes, tenant dashboard, run anomaly alerts (D-82, D-89). |
| **X7 Metrics (14)** | Done | Success metrics report generation, automated link and sentence verifier. |
| **X8 Registers (12-19)** | Done | Risk controls, decision tracking, claims labelling, messaging rules linting. |

## Gates Run

- `make migrate` and migration lint: **Pass**
- `scripts/check_conformance.py`: **Pass** (0 missing requirements across 42 rules)
- `ruff check`: **Pass** (auto-formatted)
- `mypy --strict`: **Pass** (type hints added to all new modules)
- `check_rls_catalog.sql`: **Pass** (no rows returned, RLS enforced everywhere)

## Proven by Tests vs Assumed

**Proven by tests:**
- The data model matches the executable spec.
- Exports match their exact stored rows by hash and re-extraction.
- Runs are permanently pinned (config, graph, model version).
- Drift disables an operation fail-closed.
- Regressions block merges.
- Retention and erasure remove exactly what the dry run lists, leaving tombstones.
- An erased person stays erased (suppression lists block ingestion).
- No secret or prompt reaches general logs or metrics.
- The audit chain detects tampering.
- The four unit costs reconcile precisely with the ledger.
- The anomaly alert fires exactly as specified.
- Every risk, decision, and labelled claim is tracked; unlabelled claims and messaging violations fail the build.

**Not proven (assumptions / external dependencies):**
- Any legal basis or compliance with GDPR, DPDP.
- A SOC 2 audit or report.
- The external penetration test execution.
- Real billing-export allocation (needs actual GCP data).
- Real broker drift behaviour in the wild.
- Real link-check coverage at scale (needs a live crawl).

## Decisions You Must Make (D-81 to D-90)

- **D-81 Prompt Capture:** Prompts are routed exclusively to the review store and off by default. Must decide when to enable it.
- **D-82 Unit Costs:** Approve the usage-share method for infrastructure allocation.
- **D-83 Retention against append-only:** Accept that retention erases platform-held data leaving tombstones, resolving the append-only conflict.
- **D-84 People-data scope:** Minimisation map is enforced; confirm fields.
- **D-85 Rescue definition:** Manual intervention flags are tracked as "human rescue".
- **D-86 SOC 2 readiness:** Approving automated CI/CD and access reviews as evidence.
- **D-87 Secrets and keys:** Dual-key overlap implemented. Need rotation interval choices.
- **D-88 Contract drift:** Breaking drift fails closed. Approve the policy.
- **D-89 Anomaly alert:** Approving 2x median duration rule.
- **D-90 Claims and wording:** Require "estimate" or "measured" on all figures.

## What You Must Supply

- Counsel's lawful-basis and DPA text (templates are empty).
- Actual rotation intervals (currently proposed in config).
- Retention exceptions and definitions.
- The review-minutes idle cut-off value.
- The median window configuration for anomaly detection.
- Drift thresholds.
- SOC 2 scope and auditor selection.
- Penetration-test vendor and dates.
- Billing export access credentials/table locations.

## Follow-ups & Open `[VERIFY]` Items

- **[VERIFY]** Cloud SQL backup destruction SLA for erased rows.
- **[VERIFY]** GCP billing export labels precisely match the infrastructure allocation query.
- **[VERIFY]** Object Lock retention behaviour on storage keys.
- **Follow-up:** Wire in real providers and actual Slack/Email adapters once real credentials are provided.

## Commits

`feat(crosscut): Implement X5, X6, X7, X8 cross-cutting workstreams` on branch `feat/crosscutting`.
