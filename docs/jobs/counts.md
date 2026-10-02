# Job posting count contract

Counts are calculated from distinct posting rows in code. Every status count carries a base
(`matched` or `read`). The chain is `read <= matched <= in_window <= deduped <= pulled`.
A row is read only when `text_complete` is exactly true and its description meets the
configured minimum length. Unknown completeness and excerpts do not count as read.

Platform counts may overlap because one posting can name multiple platforms. They are not
summed to obtain distinct postings. A rendered count sentence must come from
`PostingCounts`; callers never supply count values.
