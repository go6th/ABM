# Handling of people data (P0-7)

**Draft for counsel. Not legal advice.** This describes what the system does with personal data
today, so counsel can review it against the applicable law (decisions D-06 and D-10 are open and
belong to counsel and the owner, not to engineering).

## What personal data the system touches

| Data | Where it comes from | Where it is stored | Who sees it |
|---|---|---|---|
| Names, titles, seniority, profile URLs of senior people at a target account | people providers (p_org today; p_people blocked) | `people` table (tenant-scoped, RLS), raw payloads in the payload bucket | the tenant's users through deliverables |
| Executive statements in public sources (earnings calls, news) | public pages and feeds | payloads; facts with a supporting sentence | the tenant |
| Contact details incidentally present in job postings (recruiter email, phone) | job feeds and pages | raw payload only; removed from every derived field (sentences, titles, class evidence, exports) | nobody in client output |
| Names of the tenant's own staff and of other accounts | tenant input, run input | never in client output (detail scrub T9, client-output scanner) | operators only |

## Controls in code

- Tenant isolation by the database (forced RLS on every tenant table, runtime role without bypass).
- Contact details are stripped from derived job-posting fields (`jobs.normalise.strip_pii`) and a
  sentence containing them is not sent as evidence (`jobs.claims`).
- Excluded functions (security, IT compliance, IT audit) are removed from people lists
  (`config/people_scope.yaml`).
- No outreach code path exists; the system never contacts a person.
- Raw payloads live in one private, versioned, customer-key-encrypted bucket; no public access.
- Client-facing output is scanned before release (`safety.client_output`).

## Open questions for counsel

1. Lawful basis for processing professional profile data of people at prospect accounts, per
   jurisdiction of the tenant and of the person.
2. Retention period for raw payloads and people rows (D-06); the bucket has no delete rule until
   this is decided.
3. Sub-processor disclosure of data providers (the data processing agreement lists them; client
   output never names them).
4. Data subject requests: deletion and access across raw payloads, facts and deliverables.
5. Transfer: the default region is asia-south1 (D-20); whether that suits every tenant.
