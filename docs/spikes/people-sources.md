# People-source comparison protocol

**Status: Blocked.** No September account outputs, broker credits/prices or spend approval were supplied. Adapters in this repository are interfaces only; they do not call a provider.

| Account | BitScale usable senior people / cost | BitScale + Sumble usable senior people / cost | Status |
|---|---|---|---|
| — | — | — | Blocked: September snapshots, broker cost records and spend approval not supplied |

This results table intentionally has no account rows until real, approved measurements exist.

Run BitScale alone against BitScale plus Sumble on the same three accounts with identical role scope, geography, seniority, query budget, capture timestamp and deduplication rules. Sumble continues to provide organizations, technology stack and teams in both arms. Evaluate:

1. **Senior-person fill rate:** unique usable senior people after URL-first deduplication and exclusion review, divided by the analyst-approved expected senior-person set.
2. **Cost per usable senior person:** actual arm cost divided by unique usable senior people; report zero usable people as undefined, not zero cost.
3. Report per-account values and the paired aggregate, sample size, query count, profile completeness, duplicate rate and exclusions.

Use the `mode` switch in `config/people_sources.yaml` through `build_people_source_router`. Preserve both arms and do not change the configured default until D-03 is signed off. Required inputs: three September account snapshots, broker usage/cost records for both arms, and an analyst-reviewed usable-person rubric. No results are recorded until those inputs exist.
