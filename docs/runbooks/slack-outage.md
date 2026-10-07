# Runbook: Alert deliveries failing (Slack or another channel) (ticket)

Owner: TODO (alerts owner, docs/oncall/rotation.md)

## Symptom
`alert_delivery_failed`: alert deliveries ended `failed` after the 24-hour retry schedule.

## Impact
Users miss alerts on that channel; the alert stays in the in-app inbox. A delivery failure never
blocks a refresh.

## First checks
1. The channel label (slack, email, webhook) on the dashboard.
2. The channel provider's status (Slack status page, the mail provider).
3. The tenant's connection state for Slack or the webhook endpoint.

## Mitigation
Wait for the channel to recover; pending rows retry on the schedule. Failed rows are visible to
operators; they are not re-sent automatically (at-least-once ends at the window).

## Escalation
Ticket; the alerts owner.

## Verification
New deliveries on the channel are `sent`; the freshness SLI recovers.

## Follow-up
If failures were silent at the channel, add a fake-channel case.
