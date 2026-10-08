CREATE TABLE link_checks (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    run_id uuid NOT NULL,
    url text NOT NULL,
    fetch_ref text NOT NULL,
    sentence_found boolean NOT NULL,
    checked_at timestamptz NOT NULL,
    CONSTRAINT fk_link_check_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
);
ALTER TABLE link_checks ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant isolation" ON link_checks TO app_rw USING (tenant_id = current_setting('app.tenant_id')::uuid) WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT ON link_checks TO app_rw;
