# ADR-0008: Job pipeline proposals and unresolved decisions

Status: proposed, awaiting product owner. Sources: PRD sections 3.4, 4.2, Appendix A;
project job pipeline request.

## D-16: classification and full-text scope

The PRD supplies class labels and contradictory full-text phrasing. The rubric and cue
rules are proposals only. Proposed scope options are `[build, admin_owner]` (superset) and
`[build]` (narrower). Neither is selected. A title match is always a lead until description
evidence is read.

## D-17: lane keywords and aliases

The PRD names lane/platform labels but supplies no search keywords or aliases. Config keeps
these empty. Board searches cannot be reported as performed until product supplies them.

## Other proposals

Provider status is retained as unknown unless explicit status is present; live board
presence during this run makes a posting open. Undated rows are included and counted as
undated; dates before the calendar window are out of window. Derived fields redact email
and phone patterns; raw text remains in the payload store. The PII approach remains subject
to D-06/legal review. Caps in `jobs_v1.yaml` are provisional.

## Verification gaps

The connector's normalized record has no verified completeness flag. Supply one recorded
sample for each board search, job feed, and job-page read operation. Database tables and
RLS migration are deferred until a PostgreSQL test database is available to validate the
migration and isolation catalog.
