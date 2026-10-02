# Contributing

## Work process

- Branch names: `feat/<short-description>`, `fix/<short-description>`, or `chore/<short-description>`.
- Use Conventional Commits, for example `feat(db): add forced tenant RLS`.
- Follow the one-work-package loop in `.claude/skills/account-intelligence-platform/SKILL.md`: state scope and gates, test invariants first, implement the smallest increment, run gates, update `BUILD_LOG.md`, and stop at phase boundaries.
- **`make check` must pass before a PR.**

## Rules that must never be broken

1. **Isolation is enforced by the database, not by application code.** Every tenant table has a non-null tenant id and forced RLS from the first migration. Runtime `app_rw` is not the owner and has no `BYPASSRLS`. Tenant context is set only by `tenant_txn()` with transaction-local `set_config`. Never ship a single-tenant schema "for now".
2. **Tenant identity comes only from the verified token.** No endpoint, tool, or agent accepts a tenant id from a request body, path, query, or model output. Agents get no SQL, shell, or arbitrary-URL tools.
3. **Untrusted text is read only by the tool-less extractor** that emits schema-validated JSON. Nothing that reads untrusted text can send, write, or fetch. Tool arguments are typed.
4. **No fact without a source, lineage, and a grade computed by the formula.** Claims without evidence are dropped. Normalise in code, not in the model. Only Confirmed (read in a primary source this run) may be stated to a prospect. Absence of evidence is a Hypothesis, never a conclusion. A Lead needs three differently worded queries across two tools.
5. **Two outputs.** Client-facing output uses public data only. Internal material appears there only in neutral form. The internal view is called "Internal Only", never named after a person, and is never in a shared folder, workspace zip, public cache, or another tenant.
6. **Client-facing output never names a data broker, a credit count, a call recording, client staff, or another account.** Attribution is "6th agents". Brokers are named only in the DPA sub-processor list. Provider names stay in internal tables and use codes.
7. **Money is a ledger.** Reserve credits atomically with the run row, commit and refund at terminal state, one settlement per run, insert-only ledgers, no negative balance. Each paid call is recorded once per `(run_id, args_hash)`. A run never exceeds its budget cap; a cap ends in `BUDGET_EXCEEDED` with partial results.
8. **Durability:** the LangGraph checkpoint is the source of truth. Inngest only triggers, schedules, and loops time slices. One retry owner per failure class. Events carry identifiers only, never content.
9. **Never send outreach. Never delete or overwrite a client's records.** Add new rows and files; mark superseded facts instead of removing them. Sensitive findings are preparation, never openers.
10. **No silent omission.** A source failure makes its coverage row Blocked with a reason, and the run continues. Every coverage row ends Done, Partial, Not done, Blocked, or None with proof. Every count names its base.
11. **Pull once.** Each broker feed is pulled once per run and saved. Respect the adapter rules in `assets/routing.yaml`.
12. **Do not present assumptions as facts.** Source figures are working assumptions, and architecture cost/timing numbers are targets to prove. Anything tagged `[VERIFY]` stays unverified until tested or checked against first-party documentation.
