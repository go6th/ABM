-- The "Owner only" upload option and the owner-only role of workspace zip members are removed
-- (owner 2026-11-08). Every owner-only document, zip member and note becomes a client one, and
-- the audience checks stop accepting 'owner_only'. A zip member the old rule skipped keeps its
-- skipped status (it was never read) with a reason that says to upload the zip again; a new
-- upload reads it like any other file. Nothing is re-read here and no run starts.
-- The three tables force row level security with policies for app_rw only, so the migrating
-- owner sees no row (staging migrate 2026-11-08 failed on the check); the force is lifted for
-- the updates and put back in the same transaction.
ALTER TABLE documents NO FORCE ROW LEVEL SECURITY;
ALTER TABLE document_members NO FORCE ROW LEVEL SECURITY;
ALTER TABLE research_notes NO FORCE ROW LEVEL SECURITY;
UPDATE documents SET audience = 'client', updated_at = now() WHERE audience = 'owner_only';
UPDATE document_members
   SET role = 'other',
       failure = CASE WHEN failure = 'owner_only_skipped' THEN 'not_read_upload_again'
                      ELSE failure END,
       audience = 'client',
       updated_at = now()
 WHERE role = 'owner_only' OR audience = 'owner_only' OR failure = 'owner_only_skipped';
UPDATE research_notes SET audience = 'client' WHERE audience = 'owner_only';
ALTER TABLE documents FORCE ROW LEVEL SECURITY;
ALTER TABLE document_members FORCE ROW LEVEL SECURITY;
ALTER TABLE research_notes FORCE ROW LEVEL SECURITY;

ALTER TABLE documents DROP CONSTRAINT ck_documents_audience;
ALTER TABLE documents
   ADD CONSTRAINT ck_documents_audience CHECK (audience IN ('client', 'internal'));
ALTER TABLE document_members DROP CONSTRAINT ck_document_members_audience;
ALTER TABLE document_members
   ADD CONSTRAINT ck_document_members_audience CHECK (audience IN ('client', 'internal'));
ALTER TABLE research_notes DROP CONSTRAINT ck_research_notes_audience;
ALTER TABLE research_notes
   ADD CONSTRAINT ck_research_notes_audience CHECK (audience IN ('client', 'internal'));
