-- Remove the research file tables and the orphan discovery function.

DROP FUNCTION IF EXISTS research_files_orphaned(timestamptz);
DROP TABLE IF EXISTS research_file_deletions;
DROP TABLE IF EXISTS research_file_model_calls;
DROP TABLE IF EXISTS research_file_findings;
DROP TABLE IF EXISTS research_file_state;
DROP TABLE IF EXISTS research_file_chunks;
DROP TABLE IF EXISTS research_file_upload_parts;
DROP TABLE IF EXISTS research_file_uploads;
DROP TABLE IF EXISTS research_files;
