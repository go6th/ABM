-- Undo migration 0083: the entity lock columns on accounts.
ALTER TABLE accounts DROP COLUMN IF EXISTS entity_lock_at;
ALTER TABLE accounts DROP COLUMN IF EXISTS entity_lock;
