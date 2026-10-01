# Security

Report security issues privately to **TODO: security contact**. Do not file public issues containing exploit details or sensitive data. The project owner must replace this placeholder before inviting external contributors.

Tenant isolation is enforced in PostgreSQL with forced row-level security, non-owner runtime roles, and transaction-local tenant context derived from verified identity. Isolation and public/private rendering tests are release gates. This is a design guarantee to verify in each deployed environment, not a substitute for an external security assessment.
