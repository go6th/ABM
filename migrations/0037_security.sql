CREATE TABLE rotation_events (
    id bigserial PRIMARY KEY,
    secret_id text NOT NULL,
    action text NOT NULL,
    at timestamptz NOT NULL
);
GRANT SELECT, INSERT ON rotation_events TO app_rw;

CREATE TABLE soc2_evidence (
    id bigserial PRIMARY KEY,
    control_id text NOT NULL,
    snapshot_hash text NOT NULL,
    collected_at timestamptz NOT NULL
);
GRANT SELECT, INSERT ON soc2_evidence TO app_rw;

CREATE TABLE pentest_tracker (
    id bigserial PRIMARY KEY,
    status text NOT NULL,
    report_ref text,
    updated_at timestamptz NOT NULL
);
GRANT SELECT, INSERT ON pentest_tracker TO app_rw;

CREATE TABLE tool_call_records (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    run_id uuid NOT NULL,
    operation text NOT NULL,
    args_sha256 text NOT NULL,
    cost_usd integer,
    created_at timestamptz NOT NULL,
    CONSTRAINT fk_tool_call_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
);
ALTER TABLE tool_call_records ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant isolation" ON tool_call_records TO app_rw USING (tenant_id = current_setting('app.tenant_id')::uuid) WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT ON tool_call_records TO app_rw;

CREATE TABLE review_samples (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    run_id uuid NOT NULL,
    prompt_text text,
    response_text text,
    created_at timestamptz NOT NULL,
    CONSTRAINT fk_review_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
);
ALTER TABLE review_samples ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant isolation" ON review_samples TO app_rw USING (tenant_id = current_setting('app.tenant_id')::uuid) WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT ON review_samples TO app_rw;
