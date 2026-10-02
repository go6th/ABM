# Project status

Updated 2026-10-02.

## Broker connectors (WP 1.4)

Status: built on branch `feat/broker-connectors`, not merged. Code in
`src/account_intelligence/connectors/`, docs in `docs/providers/`, decisions ADR-0006
(lease and ledger) and ADR-0007 (retry owner).

| Provider | Operations | Status |
|---|---|---|
| p_search | web_search, read_page | Done |
| p_scrape | scrape_job_page (plain) | Done |
| p_signals | news_events, job_openings, tech_detections | Done |
| p_org | org_resolve, org_tech_stack, people_by_technology | Done; org_teams not specified |
| p_people | people_by_title, profile_enrichment | Blocked: needs the workspace grid contract |

Verified locally against PostgreSQL: isolation suite, RLS catalog, kill/resume in a
subprocess, two workers, 50 concurrent calls, budget caps, spend equals ledger. Open
items: `docs/providers/README.md`.
