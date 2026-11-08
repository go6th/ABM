-- Undo migration 0113: Delete in the app only marks the account again.
DROP FUNCTION purge_own_deleted_account(uuid, boolean, boolean);
