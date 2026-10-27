-- The entity lock (2026-10-24, master prompt v2 section 3): what a research locked as the
-- account before its first pull (allowed domains, the host the site redirects to, subsidiaries,
-- careers boards, blocked look-alike brands, whether the home page named the account). Written
-- by the research graph's entity_lock node once per run, read by grading (primary hosts), the
-- people scope and the job pipeline. Additive: two nullable columns.

ALTER TABLE accounts
   ADD COLUMN entity_lock jsonb,
   ADD COLUMN entity_lock_at timestamptz;
