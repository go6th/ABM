-- Undo migration 0087: the account deletion mark.
DROP INDEX IF EXISTS ix_accounts_live;
ALTER TABLE accounts DROP CONSTRAINT IF EXISTS ck_accounts_deleted_by_with_at;
ALTER TABLE accounts DROP COLUMN IF EXISTS deleted_by;
ALTER TABLE accounts DROP COLUMN IF EXISTS deleted_at;
