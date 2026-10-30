-- Deleting an account (2026-10-27): a user removes an account from their workspace after a
-- confirmation. The row is marked, never removed (every ledger, run and fact of the account
-- stays for audit and billing; insert-only tables keep their foreign keys): a deleted account
-- is hidden from every listing and lookup, and adding the same domain again revives it.
ALTER TABLE accounts
   ADD COLUMN deleted_at timestamptz,
   ADD COLUMN deleted_by text,
   ADD CONSTRAINT ck_accounts_deleted_by_with_at CHECK ((deleted_at IS NULL) = (deleted_by IS NULL));
COMMENT ON COLUMN accounts.deleted_at IS 'When the user removed the account from the workspace; NULL while it is listed.';
CREATE INDEX ix_accounts_live ON accounts (tenant_id, domain) WHERE deleted_at IS NULL;
