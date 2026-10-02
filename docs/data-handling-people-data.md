# Draft for counsel. Not legal advice.

## Purpose and scope

This draft describes proposed handling for named business people researched for account intelligence. It is an input to counsel, not a legal conclusion or approval to collect or process data. Counsel must decide applicable law and lawful basis before people data is stored at scale or used in client deliverables.

## Data potentially handled

Names, public professional titles, seniority/level, business function or lane, business profile URLs, role start dates and derived tenure, source links and quoted public professional statements, exclusion decisions with a reason, merge/deduplication pointers, capture and review dates, and a limited audit trail. Do not collect special-category or sensitive personal data, personal contact details, private social content, or client staff/call content in this people dataset. Exclusion reasons must be factual, minimal and non-sensitive.

## Proposed lawful-basis options for counsel

Counsel should assess legitimate interests (including necessity, balancing, transparency and objection handling) and any alternative basis required by the applicable jurisdiction. Public availability alone is not consent and does not decide lawfulness. Counsel must assess India DPDP Act applicability, GDPR/UK GDPR and other relevant regimes, controller/processor roles, notices, data-subject rights and restrictions on profiling or marketing use.

## Proposed retention defaults from the architecture

These are proposed technical defaults only; counsel, product and broker terms may require shorter periods:

| Data class | Proposed retention |
|---|---|
| People records and source-backed facts | Current plus history for change tracking; delete on tenant deletion/request where legally and contractually permitted |
| Raw broker payloads | 30 days hot, then archive up to 12 months, then delete or earlier on an approved request |
| Run metadata | 13 months, then archive to Parquet |
| Facts/evidence superseded history | Current/history for deltas; superseded records archived to Parquet after 24 months |
| Audit records | 90 days queryable; 7 years archived with Object Lock, subject to counsel-approved schedule |
| Wallet/financial ledger | Permanent in the architecture; confirm statutory and contractual basis/schedule with counsel |

Never infer or silently extend a retention period. Implement configurable schedules and record the actual deletion/archive event.

## Access, minimization and security

People data is tenant-scoped from the first migration, with forced row-level security and `tenant_id NOT NULL` on tenant tables. Runtime access is through the non-owner `app_rw` role. Workers and broker adapters receive only the minimum company domain/name needed; never send tenant identifiers, private client context, or another account's data to a broker. Use least-privilege access, encryption at rest/in transit, neutral raw-object keys, allow-listed logs, and access auditing. Do not expose provider identity, costs or another account's activity in client views.

## Deletion and objection handling

Proposed process: authenticate and scope the request to the tenant/person; record request receipt and deadline; stop further collection/use; locate derived people rows, facts/evidence, raw payloads and exports by source/profile identity; remove or suppress them from active stores and client-visible outputs; propagate object-store deletion; retain only records counsel confirms may/must be retained, with a restricted suppression marker where appropriate; verify completion and notify the requester. The architecture calls for document/object deletion propagation within 24 hours, but counsel/product must approve a people-data SLA. Implement a dry-run report and auditable confirmation before enabling destructive deletion. Existing people-row retention/merge behavior in this database is not a substitute for a lawful deletion workflow.

## Broker terms to confirm before production

- **D-10 / V7:** permitted collection, storage, resale and derived-data use; retention/deletion obligations; refresh/cache rights; geographic restrictions; rate limits; subprocessors; audit rights; breach notice; individual-rights support; and whether outputs can be incorporated into customer deliverables.
- Confirm that each provider's data-processing terms cover the purposes and retention above, and that the company can honor deletion/objection requests throughout downstream copies and exports.
- Confirm no broker receives private client material, tenant IDs, call recordings or more than the minimum account-identifying query data.

## Decisions still required

Counsel: jurisdiction and lawful basis; notice/objection and rights workflow; retention schedule; broker clauses; controller/processor allocation; permitted profiling and export use. Product/security: data scope and exclusion policy, role/access model, deletion SLA, and approval of the operational process. Analyst role remains an open product/RBAC decision and is intentionally not added to the role set.
