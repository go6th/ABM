-- Undo migration 0092. Rows keep their (possibly moved forward) archived_at; the index fields
-- of meta are dropped with the column (the archive index then reads the bodies again).
DROP FUNCTION IF EXISTS erase_archive_urls(uuid, text[], text);
DROP TRIGGER IF EXISTS trg_link_archive_touch_only ON link_archive;
DROP FUNCTION IF EXISTS link_archive_touch_only();
REVOKE UPDATE (archived_at) ON link_archive FROM app_rw;
ALTER TABLE link_archive DROP CONSTRAINT IF EXISTS ck_link_archive_meta;
ALTER TABLE link_archive DROP COLUMN IF EXISTS meta;
