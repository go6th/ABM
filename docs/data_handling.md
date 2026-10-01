# People data handling approach

**DRAFT FOR COUNSEL REVIEW.** Nothing in this document is legal advice or a legal conclusion. It sets out what the system collects and the options the team proposes, so counsel can advise. Every item marked "For counsel" needs an answer before people data is stored at scale (roadmap section 6, data protection review).

Sources: roadmap section 6 and section 11.4 (people data), PRD sections 3.3, 3.4 and 3.5. Earlier legal working papers from the previous design are in `docs/legal/` and can be used as inputs.

## 1. Why the system handles people data

Account Intelligence produces a deduplicated list of senior people at a target company so a seller knows who owns a platform and who to approach. Profiles of named people are personal data (roadmap section 6). The system prepares sellers and never sends outreach of any kind.

## 2. What is collected

Only what the deliverables need (roadmap section 11.4, data minimisation).

| Data | Why it is needed | Source |
|---|---|---|
| Name, current title, company | Identify the person and their role at the account | Public profiles through data brokers, company leadership pages |
| Public profile URL | Every row carries the link that proves it (PRD trust model) | Data brokers |
| Level, lane, priority | Computed by the system from the title | Derived |
| Join date, role start, tenure | Seniority and recency of the role | Public profile |
| Prior employers, certifications | Alumni and certification counts stated with their base | Public profile |
| Location | Global scope; needed to group people | Public profile |
| The person's own public description of their work at the account | Evidence of platform ownership | Public profile |
| Recent public posts and comments, priority A people only | Executive voice and current focus | Public profile, filtered to recent activity |

Not collected: personal contact details beyond what a broker returns with a profile, private messages, special category data. People in security, IT compliance and IT audit functions are excluded by rule (`config/people_scope.yaml`).

Internal material (call notes, email threads, client CRM) that names people goes only to the operator-only output and never to client files (PRD section 3.5). Client history connectors are Phase 3 work and need their own review.

## 3. Lawful basis: options for counsel

The team does not state which basis applies. The options below are for counsel to assess for each regime that applies (roadmap section 11.4 names GDPR and India's DPDP Act as examples, depending on where clients and data subjects are).

| Option | What it would require from the system | For counsel |
|---|---|---|
| Legitimate interests (GDPR) | A written balancing assessment; a clear notice route; an easy objection and deletion route | Whether B2B sales research on senior professionals can rest on this basis, and what the assessment must cover |
| Consent | A way to obtain and record consent before collection | Whether this is practical for research on people who have not contacted us |
| Exemptions for publicly available data (for example under the DPDP Act) | Evidence the data was made public by the person or under a legal duty | Whether and how any such exemption applies to broker-sourced profiles |

For counsel: which regimes apply given where clients, data subjects and hosting are; whether a notice to data subjects is required and how it would be given.

## 4. Retention windows: proposals for counsel

Proposed structure. The numbers are placeholders for counsel and the product owner to set; none is a recommendation.

| Data | Proposed trigger | Proposed window |
|---|---|---|
| People rows on an active account | While the account is monitored or under engagement | To be set |
| People rows on an inactive account | From last run or refresh | To be set |
| Raw broker responses (neutral file names) | From the run that pulled them | To be set |
| Exports containing people | From delivery | To be set |
| Audit log entries about people data | From the event | To be set |

Refresh (Phase 4) re-checks people on a shelf life. A person who has left the account moves to history rather than being overwritten (roadmap section 3 principle 5). For counsel: how long history rows may be kept.

## 5. Access and deletion requests

Proposed process, to be confirmed by counsel:

1. Intake through one published address or form; log the request with a date.
2. Verify the requester's identity proportionately.
3. Locate every record for the person by profile URL and normalised name: people rows, raw broker responses, exports, workspace archives and backups.
4. For access: produce what is held and its sources within the legal deadline.
5. For deletion: delete or anonymise the records, and add a suppression entry (a one-way hash of the profile URL) so the person is not pulled again.
6. Record completion in the audit log.

Note for the product owner: roadmap principle 5 ("never overwrite or delete client and operator records") concerns business records. A lawful deletion request about a person is a different case; counsel should confirm it takes precedence and how exports already delivered to a client are handled.

## 6. Broker data-processing terms

For each broker, before storing data at scale (roadmap section 6 and the risk register in section 15):

| Question | Why |
|---|---|
| Is a data processing agreement available and signed? | Required for processing personal data on our behalf |
| Where is data processed and stored? | Transfers and regional hosting |
| May we store responses and reuse them across runs and refreshes? | Pull once, work from rows |
| May results be shown to multiple client tenants, white-labelled or resold? | Roadmap risk: broker terms may restrict resale or white-label use |
| How do they handle deletion requests passed on to them? | Section 5 above |
| What is their own lawful basis for the data they supply? | Our basis may depend on theirs |

The broker names live in `config/routing.yaml` (internal). They are named to clients only in a subprocessor list where the law requires it.

## 7. Security controls that apply to people data

From roadmap section 11.4: row-level security and per-tenant storage prefixes (Phase 3), secrets from a secrets manager only, an immutable audit log of views and exports, and fetched web content treated as untrusted data.

## 8. Open items for counsel

1. Which data protection regimes apply, by client and data subject location.
2. Lawful basis for each regime (section 3).
3. Retention windows (section 4).
4. Whether data subjects must be notified, and how.
5. Deletion versus "never delete records", and delivered exports (section 5).
6. Review of each broker's data-processing terms and resale or white-label rights (section 6).
7. Whether regional hosting is required for any client (roadmap section 16, hosting region).
