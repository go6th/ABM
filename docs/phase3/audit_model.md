# Audit model (D-69, ADR-0059)

* Every route has an audit class in the catalog: `read`, `run`, `export`, `admin` or `none`
  (`scripts/check_route_catalog.py` refuses a route without one).
* Reads are recorded by `authorize` after the role check: actor, action, resource type and id (path
  parameters joined; values that are not identifiers are hashed), route template, request id,
  outcome. Deduplicated per actor, action and resource for `read_dedupe_window_s` (60 s,
  proposal). A read whose row cannot be written is refused (503) while `read_fail_closed` is true.
* Run, export and admin actions write their row in the action's own transaction
  (`api.audit_log.audit_action`): no audit row, no action.
* Operator actions on a tenant (`operator.*`) and support actions (`support.view`,
  `support.write`, allowed or denied) are rows in that tenant's audit, visible to its admins.
* Chain: `audit_events` is insert-only; each row stores `prev_hash` and `hash` over its canonical
  form, per tenant, under an advisory lock. Verification (`audit.chain`) walks bounded batches
  (1,000 rows, 50 batches per pass) and reports the first break (a deleted or edited row).
* Export (`GET /v1/audit/export`, admin with step-up): CSV of the tenant's rows plus connector
  access rows and provider calls **by category**; it passes `scripts/scan_client_output.py`.
* Retention: 90 days queryable and 7 years archived (ARCH 14). The archive with Object Lock is not
  built (Blocked, OI-P3-6).
