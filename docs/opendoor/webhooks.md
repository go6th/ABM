# Webhook signing

Same scheme as alert webhooks: header `X-Signature: v1=<hex>` where hex is HMAC-SHA256 with the
tenant's endpoint secret over `<X-Timestamp>.<body>`; `X-Event-Id` for deduplication. Receivers must
reject timestamps more than 5 minutes from their clock and remember event ids. Deliveries retry
with exponential backoff (first after 60 s, doubling) for 24 hours, then stop and are recorded as
failed. Implementation: `signals.webhooks.sign`, `alerts.outbox.relay`.
