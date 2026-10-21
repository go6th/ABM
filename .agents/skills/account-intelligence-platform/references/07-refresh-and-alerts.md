# 07 Scheduled refresh, deltas and alerts (SAAS 5, ARCH 6.4; merged in `assets/alert_rules.yaml`)

## Cadence follows shelf life, not one schedule per account
| Source type | Cadence | Method | Cost |
| --- | --- | --- | --- |
| Live job board by lane | Weekly | Monitor with change detection, or a weekly scrape | Low |
| Job openings feed, open and closed | Monthly | Broker pull with first-seen from the last pull | Medium |
| News events | Weekly | Feed with "found from last pull"; verify tier-one items at source | Low to medium |
| Technology detections | Monthly | Broker pull, diff against last | Medium |
| Leadership changes | Weekly | News feed categories for hires and leaves | Low |
| Earnings and filings | Quarterly, on the reporting calendar | Fetch release and transcript, re-verify figures | Low |
| Senior people list | Quarterly | Title search paged from the last pull, dedupe against the list | Medium |
| Profiles of priority A people | Quarterly | Profile pull for changed people only | Medium |
| Posts of priority A people | Monthly, only people with recent activity | Enrichment pull | Medium |
| Speaking, podcasts, content | Monthly | Person-name searches | Low |
| Review sites and communities | Quarterly | Fetch per site | Low |

The cadences are stored per account (`accounts.refresh_cadence`) and per attribute class, and checklist rows carry their own `refresh_cadence` in `assets/checklist.yaml`.

## Probe, then escalate (ARCH 6.4)
A **probe** calls only the cheapest high-signal sources (job postings, news and event feeds). It escalates to a full run when the **delta score** crosses a threshold or the account's brief is older than its maximum age. Illustrative economics (not measured): full run about $5.60, probe about $0.30, 15% escalate, expected refresh about $1.14, roughly 80% below refreshing everything. Do not re-fetch attributes whose half-life has not expired. Jitter scheduled refreshes across the hour. Probes and refreshes reserve credits and use the same ledger as runs.

## Delta record
Every refresh writes a **delta record per account**: new items, expired items moved to history, regraded items, and count changes with their base. The Summary opens with the delta. The delta feeds the alert engine, so **every alert traces to a graded, sourced fact delta** (`alerts.fact_delta_id`). Facts are append-only with `superseded_at`, which is what makes deltas reconstructible. A vanished posting becomes REMOVED only after it is absent on **two probes** (debounce).

## Alert rules (10, merged)
Immediate (email or Slack within the day): new build or admin/owner role in a lane the tenant sells; technology or commercial leadership change (two sources or one T1); tier-one news after source verification (per-account daily cap); funding or M&A at grade B or better; budget (run cost above estimate or wallet below next refresh cost, email to tenant admin).
Weekly digest: new technology detection (ignore below grade C); executive announced as speaker; priority A person posts after a quiet period; a Lead regraded to Confirmed or Probable; confidence drop on a material fact (**never real-time**).

- **Dedupe key** `(rule, account, fingerprint)`.
- Delivery: email, Slack or Teams, or a **signed webhook** (HMAC-SHA256 over timestamp and body, 5-minute tolerance, exponential retries for 24 h, event ids for de-duplication). Batch into a digest when volume exceeds the tenant's threshold.
- Tenants can add rules over any graded field, lane, grade or count threshold, **scoped to their own accounts** (a rule can never reference another tenant's account; enforce with RLS and a test).
- **Sensitive signals** (breach, layoff, lawsuit) are alerted as preparation, with the same wording rule as the files: never as an outreach hook.
- SLO: 95% of alerts delivered within 24 h of the signal.

## Public cache (conflict C9)
Optional per-tenant setting, **default off** (always off for enterprise unless asked). Holds only raw broker payloads for public queries keyed by domain and source type with a freshness window. Never holds anything a tenant said or the system concluded. A tenant must never see hit rates or anything revealing another tenant's interest in an account. Respect broker licence terms (**[VERIFY] V7**).
