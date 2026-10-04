-- Expand-only API v1 support. A pending row is visible only inside its open
-- transaction; the endpoint stores its response before committing.
CREATE TABLE idempotency_records (
   tenant_id uuid NOT NULL DEFAULT app_tenant() REFERENCES tenants(id),
   key text NOT NULL CHECK (length(key) BETWEEN 8 AND 64),
   endpoint text NOT NULL,
   request_hash text NOT NULL,
   status integer,
   response_json text,
   resource_id uuid,
   created_at timestamptz NOT NULL DEFAULT now(),
   PRIMARY KEY (tenant_id, key),
   CHECK ((status IS NULL) = (response_json IS NULL))
);

ALTER TABLE idempotency_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE idempotency_records FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON idempotency_records TO app_rw
   USING (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON idempotency_records TO app_rw;
