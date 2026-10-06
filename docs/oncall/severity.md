# Severity matrix and paging policy

Source of truth: `config/telemetry/severity.yaml` (D-59).

| Severity | Meaning | Response |
|---|---|---|
| page | users affected now, isolation risk, or blind (telemetry silent) | acknowledge in 15 min, any hour |
| ticket | degraded or burning slowly | next working day |

Pages: API availability burn (fast and slow), isolation canary or probe leak, RLS-denied spike,
telemetry down. Every page gets a postmortem (`docs/postmortems/TEMPLATE.md`).
