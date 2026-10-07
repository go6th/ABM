# Dedupe and scoring (DS1 to DS3, D-73)

* Fingerprint = sha256(type, normalised entity, normalised attributes, 7-day bucket). Unique per
  account; outlets and URLs never part of it.
* Clustering of different fingerprints, same type and entity, neighbouring buckets, by title token
  Jaccard: >= 0.8 same (evidence attaches), 0.5 to 0.8 ambiguous (linked; one alert lists both),
  below distinct.
* Score (integers, `config/scoring.yaml`, proposals): signal type 5 to 30, grade 0 to 30, source
  tier 0 to 20, recency 15/8/3 within 7/30/90 days, lane match 10, priority A 10. Components and
  config version stored with the event. Pure, monotone in grade and recency (property tests).
* Thresholds filter delivery only; events stay browsable.
