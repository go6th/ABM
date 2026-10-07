# Monitor catalog (SM1 to SM4)

| Kind | Tracker row | Signal type | Probe source | Production adapter |
|---|---|---|---|---|
| build_role_postings | job_board_by_lane | build_role_posting | yes | NotConfigured ([VERIFY] V-P4-MON-1) |
| leadership_changes | leadership_changes | leadership_change | yes (news categories) | NotConfigured |
| news_events | company_news | news_event | yes | NotConfigured |
| tech_detection_changes | technology_detections | tech_detection | no (monthly) | NotConfigured |
| facility_expansions | facility_expansions | facility_expansion | no (no cadence) | NotConfigured |
| vendor_customer_stories | vendor_customer_stories | vendor_customer_story | no (no cadence) | NotConfigured |

Every poll is a `monitor_runs` row: `ok`, `blocked` (reason), `not_configured` or `throttled`.
Inbound webhooks: provider signature and 5-minute tolerance verified, event id deduplicated, payload
kept as a quarantine reference and never trusted. A match is a Lead until read (D-75).
