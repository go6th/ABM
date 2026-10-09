-- Refuse a downgrade that would discard platform tombstones created after this migration.
DO $$
BEGIN
   IF EXISTS (SELECT 1 FROM platform_tombstones) THEN
      RAISE EXCEPTION 'cannot downgrade while platform_tombstones contains records';
   END IF;
END
$$;

REVOKE EXECUTE ON FUNCTION write_platform_tombstone(text, text, text, text, timestamptz) FROM app_rw;
DROP FUNCTION write_platform_tombstone(text, text, text, text, timestamptz);
DROP TABLE platform_tombstones;

DROP TRIGGER trg_people_profile_url_erasure_only ON people;
DROP FUNCTION people_profile_url_erasure_only();
REVOKE UPDATE (profile_url, profile_url_canonical) ON people FROM app_rw;
DO $$
BEGIN
   IF EXISTS (
      SELECT 1 FROM people
      WHERE erased_at IS NOT NULL AND profile_url IS NULL AND provider_person_id IS NULL
   ) THEN
      RAISE EXCEPTION 'cannot downgrade: erased people rely on the relaxed identity constraint';
   END IF;
END
$$;
ALTER TABLE people DROP CONSTRAINT ck_people_identity;
ALTER TABLE people ADD CONSTRAINT ck_people_identity CHECK (
   profile_url IS NOT NULL OR provider_person_id IS NOT NULL
);

DROP POLICY tenant_isolation ON tombstones;
ALTER TABLE tombstones NO FORCE ROW LEVEL SECURITY;
ALTER TABLE tombstones DISABLE ROW LEVEL SECURITY;
ALTER TABLE tombstones ALTER COLUMN tenant_id DROP NOT NULL;

DROP POLICY tenant_isolation ON tool_call_records;
CREATE POLICY "tenant isolation" ON tool_call_records TO app_rw
   USING (tenant_id = current_setting('app.tenant_id')::uuid)
   WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
DROP POLICY tenant_isolation ON review_samples;
CREATE POLICY "tenant isolation" ON review_samples TO app_rw
   USING (tenant_id = current_setting('app.tenant_id')::uuid)
   WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
DROP POLICY tenant_isolation ON unit_cost_rollups;
CREATE POLICY "tenant isolation" ON unit_cost_rollups TO app_rw
   USING (tenant_id = current_setting('app.tenant_id')::uuid)
   WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
DROP POLICY tenant_isolation ON run_anomaly_events;
CREATE POLICY "tenant isolation" ON run_anomaly_events TO app_rw
   USING (tenant_id = current_setting('app.tenant_id')::uuid)
   WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
DROP POLICY tenant_isolation ON link_checks;
CREATE POLICY "tenant isolation" ON link_checks TO app_rw
   USING (tenant_id = current_setting('app.tenant_id')::uuid)
   WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);

ALTER TABLE tool_call_records NO FORCE ROW LEVEL SECURITY;
ALTER TABLE review_samples NO FORCE ROW LEVEL SECURITY;
ALTER TABLE unit_cost_rollups NO FORCE ROW LEVEL SECURITY;
ALTER TABLE run_anomaly_events NO FORCE ROW LEVEL SECURITY;
ALTER TABLE link_checks NO FORCE ROW LEVEL SECURITY;
