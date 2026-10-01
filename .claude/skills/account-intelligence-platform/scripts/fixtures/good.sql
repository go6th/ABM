CREATE TABLE facts (
  id uuid PRIMARY KEY,
  tenant_id uuid NOT NULL,
  visibility text NOT NULL
);
ALTER TABLE facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE facts FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON facts TO app_rw
  USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
CREATE POLICY public_only ON facts FOR SELECT TO public_reader
  USING (tenant_id = (SELECT app_tenant()) AND visibility = 'public');
