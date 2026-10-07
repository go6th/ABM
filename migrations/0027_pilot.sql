-- Migration 0027: pilot feedback and seller usefulness (Phase 3 WS9; expand only). Spec
-- docs/prompts/build_phase3.md s5, PL1, PL2, D-68. down_revision 0026.
--
-- run_feedback: insert-only; a member's verdict on one account or one table row (useful or not,
--   a reason from config/pilot.yaml, an optional note stored as tenant data and never logged).
--   The newest row per (user, target) is the member's current verdict.
-- usefulness_events: insert-only, identifiers only (D-68): a first-call question used (marked or
--   copied) or a Confirmed fact referenced (opened, copied into a note, or in a downloaded
--   export). One row per (kind, subject, user, via, day) by the unique dedupe key.
-- Definer-rights lookup (owner pilot_reader, NOLOGIN, column grants only):
--   pilot_aggregate(p_since): per-tenant COUNTS for the operator aggregate; no content, no note,
--   no subject identifier.
-- Rollback: 0027_pilot.down.sql.

CREATE TABLE run_feedback (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   account_id   uuid NOT NULL,
   run_id       uuid,
   scope        text NOT NULL,
   table_kind   text,
   row_id       text,
   verdict      text NOT NULL,
   reason_code  text NOT NULL,
   note         text,
   user_id      text NOT NULL,
   at           timestamptz NOT NULL,
   CONSTRAINT pk_run_feedback PRIMARY KEY (id),
   CONSTRAINT fk_run_feedback_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id),
   CONSTRAINT ck_run_feedback_scope CHECK (scope IN ('account', 'row')),
   CONSTRAINT ck_run_feedback_row CHECK (scope = 'account' OR (run_id IS NOT NULL
      AND table_kind IN ('people', 'jobs', 'tech', 'facts', 'coverage')
      AND char_length(row_id) BETWEEN 1 AND 200)),
   CONSTRAINT ck_run_feedback_verdict CHECK (verdict IN ('useful', 'not_useful')),
   CONSTRAINT ck_run_feedback_reason CHECK (reason_code ~ '^[a-z_]{1,40}$'),
   CONSTRAINT ck_run_feedback_note CHECK (note IS NULL OR char_length(note) <= 2000)
);
CREATE INDEX ix_run_feedback_account ON run_feedback (tenant_id, account_id, at);
ALTER TABLE run_feedback ENABLE ROW LEVEL SECURITY;
ALTER TABLE run_feedback FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON run_feedback TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON run_feedback TO app_rw;
GRANT USAGE ON SEQUENCE run_feedback_id_seq TO app_rw;

CREATE TABLE usefulness_events (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   kind         text NOT NULL,
   subject_id   uuid NOT NULL,
   account_id   uuid NOT NULL,
   via          text NOT NULL,
   user_id      text NOT NULL,
   dedupe_key   text NOT NULL,
   at           timestamptz NOT NULL,
   CONSTRAINT pk_usefulness_events PRIMARY KEY (id),
   CONSTRAINT fk_usefulness_events_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id),
   CONSTRAINT uq_usefulness_events_dedupe UNIQUE (tenant_id, dedupe_key),
   CONSTRAINT ck_usefulness_events_kind CHECK (kind IN ('first_call_question_used',
      'confirmed_fact_referenced')),
   CONSTRAINT ck_usefulness_events_via CHECK (via IN ('marked', 'copied', 'opened', 'noted',
      'exported')),
   CONSTRAINT ck_usefulness_events_pair CHECK ((kind = 'first_call_question_used'
      AND via IN ('marked', 'copied')) OR (kind = 'confirmed_fact_referenced'
      AND via IN ('opened', 'noted', 'exported')))
);
CREATE INDEX ix_usefulness_events_at ON usefulness_events (tenant_id, kind, at);
ALTER TABLE usefulness_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE usefulness_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON usefulness_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON usefulness_events TO app_rw;
GRANT USAGE ON SEQUENCE usefulness_events_id_seq TO app_rw;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pilot_reader') THEN
    CREATE ROLE pilot_reader NOLOGIN NOBYPASSRLS;
  END IF;
END $$;
CREATE POLICY pilot_aggregate ON usefulness_events FOR SELECT TO pilot_reader USING (true);
GRANT SELECT (tenant_id, kind, at) ON usefulness_events TO pilot_reader;
CREATE POLICY pilot_aggregate ON run_feedback FOR SELECT TO pilot_reader USING (true);
GRANT SELECT (tenant_id, verdict, at) ON run_feedback TO pilot_reader;

-- reviewed: SECURITY DEFINER (owner pilot_reader, NOLOGIN, column grants on tenant, kind or
-- verdict and time only). Returns per-tenant counts since p_since; no content, note or subject.
CREATE FUNCTION pilot_aggregate(p_since timestamptz)
   RETURNS TABLE (tenant_id uuid, questions_used bigint, facts_referenced bigint,
                  useful bigint, not_useful bigint)
   LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   WITH u AS (
      SELECT e.tenant_id,
             count(*) FILTER (WHERE e.kind = 'first_call_question_used') AS q,
             count(*) FILTER (WHERE e.kind = 'confirmed_fact_referenced') AS f
        FROM usefulness_events e WHERE e.at >= p_since GROUP BY e.tenant_id),
   fb AS (
      SELECT r.tenant_id,
             count(*) FILTER (WHERE r.verdict = 'useful') AS y,
             count(*) FILTER (WHERE r.verdict = 'not_useful') AS n
        FROM run_feedback r WHERE r.at >= p_since GROUP BY r.tenant_id)
   SELECT coalesce(u.tenant_id, fb.tenant_id), coalesce(u.q, 0), coalesce(u.f, 0),
          coalesce(fb.y, 0), coalesce(fb.n, 0)
     FROM u FULL JOIN fb ON fb.tenant_id = u.tenant_id
$$;
ALTER FUNCTION pilot_aggregate(timestamptz) OWNER TO pilot_reader;
REVOKE ALL ON FUNCTION pilot_aggregate(timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pilot_aggregate(timestamptz) TO app_rw;
