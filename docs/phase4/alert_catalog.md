# Alert catalog and wording rules (AL1 to AL8, D-74 to D-76)

| Rule | Tier | Signals | Grade floor | Source |
|---|---|---|---|---|
| new_build_role | immediate | build_role_posting | probable | alert_rules.yaml |
| leadership_change | immediate | leadership_change (two sources or one T1) | probable | alert_rules.yaml |
| tier_one_news | immediate | news_event (read at source) | confirmed | alert_rules.yaml |
| funding_ma | immediate | funding_ma | probable ("B or better") | alert_rules.yaml |
| budget | immediate (admin) | not a fact delta: notification outbox (C29) | - | alert_rules.yaml |
| new_tech_detection | digest | tech_detection | hypothesis ("ignore below C") | alert_rules.yaml |
| exec_speaking | digest | exec_speaking | hypothesis | alert_rules.yaml |
| priority_a_posts | digest | priority_a_post | hypothesis | alert_rules.yaml |
| lead_regraded_up | digest | lead_regraded | probable | alert_rules.yaml |
| confidence_drop | digest (never real time) | confidence_drop | lead | alert_rules.yaml |
| facility_expansion | digest | facility_expansion | probable | proposal |
| vendor_customer_story | digest | vendor_customer_story | probable | proposal |

Wording: every text by template; Leads labelled "Lead, not yet verified"; breach, layoff and lawsuit
only as "Preparation only, not an opening line." with a neutral subject, digest only; the outreach
lint and the client-output scan run on every alert, digest, Slack message, email, webhook body and
CRM payload. Delivery: outbox per (alert, channel, recipient), at least once, event id = outbox id,
webhooks signed HMAC-SHA256 over timestamp and body with a 5-minute tolerance and exponential
retries for 24 h. Freshness SLI: delivered within 24 h of the signal, SLO 95%.
