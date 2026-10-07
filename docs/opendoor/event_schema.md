# Event schema: `signal_event.v1`

Source of truth: `src/opendoor/schemas/signal_event.v1.json` (JSON Schema 2020-12, closed object).

| Field | Type | Meaning |
|---|---|---|
| schema | "signal_event.v1" | the contract version |
| event_id | uuid | the signal event (stable) |
| account | domain | the tenant's account |
| signal_type | string | e.g. build_role_posting, leadership_change, news_event |
| fingerprint | 64 hex | the deduplication identity (one event, one fingerprint) |
| grade | confirmed, probable, hypothesis, lead | from the grading engine |
| lead | boolean | not yet verified |
| sensitive | boolean | breach, layoff or lawsuit: preparation only |
| source_codes | S-NNNN[] | the account's stable source codes |
| score | integer | significance (explainable components kept internally) |
| detected_at | date-time | when the signal was detected |
| fact_ref | uuid | the graded fact |

Never included: raw provider payloads, provider names, credit counts, costs, other accounts.
Example: the `examples` array in the schema file.
