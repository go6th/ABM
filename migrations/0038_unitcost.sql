-- Tenant-scoped unit-cost rollups and anomaly events (revision 0038).
CREATE TABLE unit_cost_rollups (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    run_id uuid NOT NULL,
    broker_credits integer NOT NULL,
    model_tokens integer NOT NULL,
    infra_usd integer NOT NULL,
    review_seconds integer NOT NULL,
    calculated_at timestamptz NOT NULL,
    CONSTRAINT fk_unit_cost_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
);
ALTER TABLE unit_cost_rollups ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant isolation" ON unit_cost_rollups TO app_rw USING (tenant_id = current_setting('app.tenant_id')::uuid) WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT ON unit_cost_rollups TO app_rw;

CREATE TABLE run_anomaly_events (
    id bigserial PRIMARY KEY,
    tenant_id uuid NOT NULL,
    run_id uuid NOT NULL,
    anomaly_type text NOT NULL,
    detected_at timestamptz NOT NULL,
    CONSTRAINT fk_anomaly_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
);
ALTER TABLE run_anomaly_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant isolation" ON run_anomaly_events TO app_rw USING (tenant_id = current_setting('app.tenant_id')::uuid) WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
GRANT SELECT, INSERT ON run_anomaly_events TO app_rw;
