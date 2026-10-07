# ADR-0063: Fingerprints, clustering and significance (D-73)

- Status: Proposed
- Date: 2026-10-07
- Deciders: TODO (product owner)

## Context

One event must be one alert, near-duplicates from different outlets are common, and keyword
matches cause false positives.

## Decision

* Fingerprint: sha256 over type, normalised entity, normalised attributes and a 7-day date
  bucket; outlet and URL never included. Unique per account in `signal_events`; a second outlet,
  a re-fetch, a replay or a later probe attaches evidence.
* Near-duplicates with a different fingerprint: token Jaccard over titles in neighbouring buckets:
  >= 0.8 same (attach), 0.5 to 0.8 ambiguous (a separate event LINKED to the other, one alert
  lists both), below distinct (proposals).
* Score: an integer weighted sum (signal type, grade, source tier, recency, lane, priority A) from
  `config/scoring.yaml`; components and config version stored with the event; no model.

## Consequences

Proven by `test_ws3_ws4_signals.py` (five outlets, one event; ambiguous linked; properties).
