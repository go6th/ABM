# Security

Report vulnerabilities privately to the repository maintainer (GitHub
[@Sakib-11](https://github.com/Sakib-11)). Do not put exploit details, credentials, or tenant data
in issues, pull requests, or discussions.

Tenant isolation is enforced in PostgreSQL with forced row-level security, non-owner runtime roles,
and transaction-local tenant context derived from verified identity. Isolation and public/private
rendering tests are release gates. This is a design guarantee to verify in each deployed
environment, not a substitute for an external security assessment.
