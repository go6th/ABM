# Connector scope policy and the off-limits procedure (D-65, ADR-0055)

## Policy

1. A tenant admin (with step-up) connects each source separately; consent is per source.
2. OAuth with PKCE; the state is bound to tenant, user and connection; the redirect URI is fixed.
3. Granted scopes must equal `config/connectors.yaml` exactly; a wider or different grant is
   refused and nothing is stored. Scopes are [VERIFY] (V-P3-CN-1 to 3).
4. No write call exists in any adapter. Content lands in quarantine only; parsing, redaction and
   embedding are the Phase B pipeline.
5. Google Drive: only items inside the folders the admin named, decided by **resolved ancestry**
   (every parent chain walked to a named folder). Shortcuts, multiple parents, shared drives and
   unknown ancestry are denied unless explicitly allow-listed.
6. Every access, allowed or refused, is a `connector_access_log` row (no token, no content);
   refusals also count in `connector_access_refused_total` (runbook
   `docs/runbooks/connector-grant-broken.md`).
7. Fetch is page-bounded, quota-bounded and resumable by cursor; token refresh is bounded, then the
   connection is marked broken.
8. Gmail and call notes are flagged off and raise `NotConfigured` until D-65 is decided.

## Off-limits procedure

A folder or object that belongs to the client but surfaces outside the named folders is put in
`connector_quarantine` (status `held`) and is unreadable. Only a tenant admin can clear or refuse
it, in Connectors; the decision is recorded once (`decided_by`, `decided_at`, a conditional update
on `held` only) and in the tenant audit (`connector_clear`, `connector_refuse`). Support and
operators can neither read nor clear a quarantined item.
