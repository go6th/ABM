CREATE TABLE notes (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL
);
ALTER TABLE notes ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON notes USING (tenant_id = (SELECT app_tenant()));
CREATE ROLE sneaky LOGIN BYPASSRLS;
