# People counts contract (generated; do not edit)

Built only by `people.counts.compute_counts` from rows; no function takes a count.
Invariants: `found = distinct + merged_away`; `distinct = in_scope + out_of_scope +
excluded + review + unresolved`; `tenure_known + tenure_unknown + date_invalid =
profiles_read`; `cert_field_provided + cert_field_absent = profiles_read`.
Unknown is never zero: alumni is `not computed` without a reference list
(reason `waiting_on_account_owner`).

| Count | Base |
|---|---|
| `found` | every record a source returned plus every person named in research |
| `distinct` | found minus merged_away |
| `merged_away` | records that point at another record (merged) |
| `possible_duplicate` | distinct rows flagged, never merged |
| `in_scope` | distinct |
| `out_of_scope` | distinct, by reason |
| `excluded` | distinct, by reason |
| `review` | distinct, by reason |
| `unresolved` | distinct (no profile URL yet) |
| `profiles_requested` | in-scope people whose profile was asked for |
| `profiles_read` | in-scope people with a read or reused profile |
| `profiles_partial` | profiles read with a field of the wrong type |
| `profiles_failed` | profiles requested whose pull failed |
| `alumni` | profiles read (not computed without a reference list) |
| `tenure_known` | profiles read with a join or start date in one bucket |
| `tenure_unknown` | profiles read with no date or a date spanning two buckets |
| `date_invalid` | profiles read with a future date or a role start before the join date |
| `tenure_buckets` | tenure_known |
| `cert_field_provided` | profiles read that provide the certifications field |
| `cert_field_absent` | profiles read that do not provide it (not 'none listed') |
| `certified_by_lane` | cert_field_provided, per lane (never summed) |
| `posts_people` | priority A people with at least one stored post |
| `posts_pulled` | posts stored |
| `activity_unfiltered` | people whose posts were pulled without a last-activity date |
| `hostile` | profiles and posts carrying the canary or an instruction |
| `fields_dropped` | provider fields outside the allow-list, dropped at the boundary |
| `by_lane_in_scope` | in_scope, per lane (never summed) |
| `by_priority` | in_scope |
