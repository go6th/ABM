-- Undo migration 0115: the audience checks accept 'owner_only' again. The rows 0115 turned into
-- client ones stay client (which were owner-only is not recorded).
ALTER TABLE documents DROP CONSTRAINT ck_documents_audience;
ALTER TABLE documents
   ADD CONSTRAINT ck_documents_audience CHECK (audience IN ('client', 'owner_only', 'internal'));
ALTER TABLE document_members DROP CONSTRAINT ck_document_members_audience;
ALTER TABLE document_members
   ADD CONSTRAINT ck_document_members_audience
      CHECK (audience IN ('client', 'owner_only', 'internal'));
ALTER TABLE research_notes DROP CONSTRAINT ck_research_notes_audience;
ALTER TABLE research_notes
   ADD CONSTRAINT ck_research_notes_audience
      CHECK (audience IN ('client', 'owner_only', 'internal'));
