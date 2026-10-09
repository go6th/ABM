-- RLS audit correction: keep tenant-owned tombstones separate from platform retention records.
-- The platform rows are copied before they are removed from the old mixed-scope table.

CREATE TABLE platform_tombstones (
   id             bigserial NOT NULL,
   subject_type   text NOT NULL,
   subject_id     text NOT NULL,
   reason         text NOT NULL,
   content_sha256 text,
   at             timestamptz NOT NULL,
   CONSTRAINT pk_platform_tombstones PRIMARY KEY (id),
   CONSTRAINT uq_platform_tombstones_subject UNIQUE (subject_type, subject_id)
);

INSERT INTO platform_tombstones (id, subject_type, subject_id, reason, content_sha256, at)
SELECT id, subject_type, subject_id, reason, content_sha256, at
FROM tombstones
WHERE tenant_id IS NULL;

SELECT setval(
   'platform_tombstones_id_seq',
   GREATEST(COALESCE((SELECT max(id) FROM platform_tombstones), 1), 1),
   EXISTS (SELECT 1 FROM platform_tombstones)
);

DELETE FROM tombstones WHERE tenant_id IS NULL;
ALTER TABLE tombstones ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE tombstones ENABLE ROW LEVEL SECURITY;
ALTER TABLE tombstones FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tombstones TO app_rw
   USING (tenant_id = app_tenant())
   WITH CHECK (tenant_id = app_tenant());

-- DSAR erasure also removes the canonical display URL; the 0014 column grant omitted it.
ALTER TABLE people DROP CONSTRAINT ck_people_identity;
ALTER TABLE people ADD CONSTRAINT ck_people_identity CHECK (
   erased_at IS NOT NULL OR profile_url IS NOT NULL OR provider_person_id IS NOT NULL
);
GRANT UPDATE (profile_url, profile_url_canonical) ON people TO app_rw;

-- The canonical URL is mutable only to clear it during a first erasure.
CREATE FUNCTION people_profile_url_erasure_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
   IF (NEW.profile_url IS DISTINCT FROM OLD.profile_url
       OR NEW.profile_url_canonical IS DISTINCT FROM OLD.profile_url_canonical)
      AND (OLD.erased_at IS NOT NULL OR NEW.erased_at IS NULL
           OR NEW.profile_url IS NOT NULL OR NEW.profile_url_canonical IS NOT NULL) THEN
      RAISE EXCEPTION 'people: profile URLs may only be cleared during erasure'
         USING ERRCODE = 'insufficient_privilege';
   END IF;
   RETURN NEW;
END
$$;
CREATE TRIGGER trg_people_profile_url_erasure_only
   BEFORE UPDATE OF profile_url, profile_url_canonical ON people
   FOR EACH ROW EXECUTE FUNCTION people_profile_url_erasure_only();

-- reviewed: SECURITY DEFINER (migration owner): inserts one content-free platform tombstone only;
-- it cannot read tenant data or set a tenant id. The search path is pinned and EXECUTE is app_rw-only.
CREATE FUNCTION write_platform_tombstone(
   p_subject_type text,
   p_subject_id text,
   p_reason text,
   p_content_sha256 text,
   p_at timestamptz
-- reviewed: writes a platform tombstone only and has no tenant-data read path.
) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
   INSERT INTO platform_tombstones (subject_type, subject_id, reason, content_sha256, at)
   VALUES (p_subject_type, p_subject_id, p_reason, p_content_sha256, p_at)
   ON CONFLICT (subject_type, subject_id) DO NOTHING;
   RETURN FOUND;
END
$$;
REVOKE ALL ON FUNCTION write_platform_tombstone(text, text, text, text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION write_platform_tombstone(text, text, text, text, timestamptz) TO app_rw;

DROP POLICY IF EXISTS "tenant isolation" ON tool_call_records;
CREATE POLICY tenant_isolation ON tool_call_records TO app_rw
   USING (tenant_id = app_tenant())
   WITH CHECK (tenant_id = app_tenant());
DROP POLICY IF EXISTS "tenant isolation" ON review_samples;
CREATE POLICY tenant_isolation ON review_samples TO app_rw
   USING (tenant_id = app_tenant())
   WITH CHECK (tenant_id = app_tenant());
DROP POLICY IF EXISTS "tenant isolation" ON unit_cost_rollups;
CREATE POLICY tenant_isolation ON unit_cost_rollups TO app_rw
   USING (tenant_id = app_tenant())
   WITH CHECK (tenant_id = app_tenant());
DROP POLICY IF EXISTS "tenant isolation" ON run_anomaly_events;
CREATE POLICY tenant_isolation ON run_anomaly_events TO app_rw
   USING (tenant_id = app_tenant())
   WITH CHECK (tenant_id = app_tenant());
DROP POLICY IF EXISTS "tenant isolation" ON link_checks;
CREATE POLICY tenant_isolation ON link_checks TO app_rw
   USING (tenant_id = app_tenant())
   WITH CHECK (tenant_id = app_tenant());

ALTER TABLE tool_call_records FORCE ROW LEVEL SECURITY;
ALTER TABLE review_samples FORCE ROW LEVEL SECURITY;
ALTER TABLE unit_cost_rollups FORCE ROW LEVEL SECURITY;
ALTER TABLE run_anomaly_events FORCE ROW LEVEL SECURITY;
ALTER TABLE link_checks FORCE ROW LEVEL SECURITY;
