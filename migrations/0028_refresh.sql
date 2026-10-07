-- Migration 0028: refresh engine (Phase 4 WS1, WS2; expand only). Spec docs/prompts/build_phase4.md
-- s5, RF1 to RF8, SL1, D-71, D-72. down_revision 0027.
--
-- shelf_life_overrides: insert-only tenant overrides of a signal's shelf life (newest wins; bounds
--   checked in code: overrides only lengthen).
-- refresh_slots: one row per (account, kind, period), unique, so a duplicate tick, a duplicate
--   delivery or an overlapping manual run produces one execution. Mutable state (documented):
--   pending -> claimed -> done | failed | skipped by conditional updates only.
-- refresh_runs: insert-only; one probe or refresh with its escalation decision and inputs.
-- refresh_row_plans: insert-only; the per-row plan (rerun or not due, carried from which run).
-- signal_history: insert-only; an expired signal (the fact keeps superseded_at) with its reason
--   and the run that expired it. Nothing is ever deleted.
-- deltas, delta_items: insert-only; the delta record, the only input to the Summary diff and the
--   alert engine. An empty delta is a row with no_change = true.
-- missing_observations: insert-only debounce observations (present, absent, blocked).
-- Rollback: 0028_refresh.down.sql.

CREATE TABLE shelf_life_overrides (
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   signal       text NOT NULL,
   version      integer NOT NULL,
   days         integer NOT NULL,
   set_by       text NOT NULL,
   reason_code  text NOT NULL,
   created_at   timestamptz NOT NULL,
   CONSTRAINT pk_shelf_life_overrides PRIMARY KEY (tenant_id, signal, version),
   CONSTRAINT fk_shelf_life_overrides_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_shelf_life_overrides_signal CHECK (signal ~ '^[a-z_0-9]{1,60}$'),
   CONSTRAINT ck_shelf_life_overrides_days CHECK (days BETWEEN 1 AND 3650 AND version >= 1),
   CONSTRAINT ck_shelf_life_overrides_reason CHECK (reason_code ~ '^[a-z_]{1,40}$')
);
ALTER TABLE shelf_life_overrides ENABLE ROW LEVEL SECURITY;
ALTER TABLE shelf_life_overrides FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON shelf_life_overrides TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON shelf_life_overrides TO app_rw;

CREATE TABLE refresh_slots (
   id           uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   account_id   uuid NOT NULL,
   kind         text NOT NULL,
   period       text NOT NULL,
   due_at       timestamptz NOT NULL,
   status       text NOT NULL DEFAULT 'pending',
   claimed_at   timestamptz,
   claimed_by   text,
   finished_at  timestamptz,
   outcome      text,
   CONSTRAINT pk_refresh_slots PRIMARY KEY (id),
   CONSTRAINT fk_refresh_slots_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id),
   CONSTRAINT uq_refresh_slots_slot UNIQUE (tenant_id, account_id, kind, period),
   CONSTRAINT ck_refresh_slots_kind CHECK (kind IN ('probe', 'refresh')),
   CONSTRAINT ck_refresh_slots_period CHECK (period ~ '^[0-9]{4}-(W[0-9]{2}|[0-9]{2}|Q[1-4])$'),
   CONSTRAINT ck_refresh_slots_status CHECK (status IN ('pending', 'claimed', 'done', 'failed',
      'skipped')),
   CONSTRAINT ck_refresh_slots_claim CHECK ((status = 'pending') = (claimed_at IS NULL)),
   CONSTRAINT ck_refresh_slots_outcome CHECK (outcome IS NULL OR outcome ~ '^[a-z_]{1,40}$')
);
CREATE INDEX ix_refresh_slots_due ON refresh_slots (status, due_at);
ALTER TABLE refresh_slots ENABLE ROW LEVEL SECURITY;
ALTER TABLE refresh_slots FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON refresh_slots TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON refresh_slots TO app_rw;
GRANT UPDATE (status, claimed_at, claimed_by, finished_at, outcome) ON refresh_slots TO app_rw;

CREATE TABLE refresh_runs (
   id             uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   slot_id        uuid,
   account_id     uuid NOT NULL,
   kind           text NOT NULL,
   parent_run_id  uuid,
   run_id         uuid,
   decision       jsonb NOT NULL,
   created_at     timestamptz NOT NULL,
   CONSTRAINT pk_refresh_runs PRIMARY KEY (id),
   CONSTRAINT fk_refresh_runs_account FOREIGN KEY (tenant_id, account_id)
      REFERENCES accounts (tenant_id, id),
   CONSTRAINT fk_refresh_runs_slot FOREIGN KEY (slot_id) REFERENCES refresh_slots (id),
   CONSTRAINT uq_refresh_runs_slot UNIQUE (slot_id),
   CONSTRAINT ck_refresh_runs_kind CHECK (kind IN ('probe', 'refresh')),
   CONSTRAINT ck_refresh_runs_decision CHECK (jsonb_typeof(decision) = 'object'
      AND octet_length(decision::text) <= 16384)
);
ALTER TABLE refresh_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE refresh_runs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON refresh_runs TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON refresh_runs TO app_rw;

CREATE TABLE refresh_row_plans (
   tenant_id         uuid NOT NULL DEFAULT app_tenant(),
   refresh_run_id    uuid NOT NULL,
   row_key           text NOT NULL,
   action            text NOT NULL,
   reason            text NOT NULL,
   carried_from_run  uuid,
   CONSTRAINT pk_refresh_row_plans PRIMARY KEY (refresh_run_id, row_key),
   CONSTRAINT fk_refresh_row_plans_run FOREIGN KEY (refresh_run_id) REFERENCES refresh_runs (id),
   CONSTRAINT ck_refresh_row_plans_action CHECK (action IN ('rerun', 'not_due')),
   CONSTRAINT ck_refresh_row_plans_reason CHECK (reason IN ('high_priority', 'shelf_life',
      'probe_source', 'not_due', 'no_cadence')),
   CONSTRAINT ck_refresh_row_plans_carry CHECK ((action = 'not_due') = (carried_from_run IS NOT NULL))
);
ALTER TABLE refresh_row_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE refresh_row_plans FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON refresh_row_plans TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON refresh_row_plans TO app_rw;

CREATE TABLE signal_history (
   id               bigserial NOT NULL,
   tenant_id        uuid NOT NULL DEFAULT app_tenant(),
   fact_id          uuid NOT NULL,
   account_id       uuid NOT NULL,
   signal_type      text NOT NULL,
   reason           text NOT NULL,
   expired_by_run   uuid NOT NULL,
   expired_at       timestamptz NOT NULL,
   CONSTRAINT pk_signal_history PRIMARY KEY (id),
   CONSTRAINT fk_signal_history_fact FOREIGN KEY (fact_id) REFERENCES facts (id),
   CONSTRAINT fk_signal_history_run FOREIGN KEY (expired_by_run) REFERENCES refresh_runs (id),
   CONSTRAINT uq_signal_history_fact UNIQUE (fact_id),
   CONSTRAINT ck_signal_history_reason CHECK (reason IN ('shelf_life', 'removed_after_probes',
      'superseded_in_refresh'))
);
ALTER TABLE signal_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE signal_history FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON signal_history TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON signal_history TO app_rw;
GRANT USAGE ON SEQUENCE signal_history_id_seq TO app_rw;

CREATE TABLE deltas (
   id               uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id        uuid NOT NULL DEFAULT app_tenant(),
   account_id       uuid NOT NULL,
   refresh_run_id   uuid NOT NULL,
   no_change        boolean NOT NULL,
   checked          text[] NOT NULL,
   checked_at       timestamptz NOT NULL,
   score            integer NOT NULL,
   CONSTRAINT pk_deltas PRIMARY KEY (id),
   CONSTRAINT fk_deltas_run FOREIGN KEY (refresh_run_id) REFERENCES refresh_runs (id),
   CONSTRAINT uq_deltas_run UNIQUE (refresh_run_id),
   CONSTRAINT ck_deltas_score CHECK (score >= 0)
);
ALTER TABLE deltas ENABLE ROW LEVEL SECURITY;
ALTER TABLE deltas FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON deltas TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON deltas TO app_rw;

CREATE TABLE delta_items (
   id           uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   delta_id     uuid NOT NULL,
   position     integer NOT NULL,
   kind         text NOT NULL,
   signal_type  text NOT NULL,
   identity     text NOT NULL,
   fact_id      uuid,
   before_value text,
   after_value  text,
   direction    text,
   base         text,
   source_code  text,
   CONSTRAINT pk_delta_items PRIMARY KEY (id),
   CONSTRAINT fk_delta_items_delta FOREIGN KEY (delta_id) REFERENCES deltas (id),
   CONSTRAINT uq_delta_items_position UNIQUE (delta_id, position),
   CONSTRAINT ck_delta_items_kind CHECK (kind IN ('new', 'expired', 'regraded', 'count_change')),
   CONSTRAINT ck_delta_items_fact CHECK (kind = 'count_change' OR fact_id IS NOT NULL),
   CONSTRAINT ck_delta_items_direction CHECK (direction IS NULL OR direction IN ('up', 'down')),
   CONSTRAINT ck_delta_items_code CHECK (source_code IS NULL OR source_code ~ '^S-[0-9]{4,}$')
);
ALTER TABLE delta_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE delta_items FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON delta_items TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON delta_items TO app_rw;

CREATE TABLE missing_observations (
   id            bigserial NOT NULL,
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   account_id    uuid NOT NULL,
   item_key      text NOT NULL,
   probe_run_id  uuid NOT NULL,
   observation   text NOT NULL,
   state_after   text NOT NULL,
   absences      integer NOT NULL,
   at            timestamptz NOT NULL,
   CONSTRAINT pk_missing_observations PRIMARY KEY (id),
   CONSTRAINT fk_missing_observations_run FOREIGN KEY (probe_run_id) REFERENCES refresh_runs (id),
   CONSTRAINT uq_missing_observations_probe UNIQUE (probe_run_id, item_key),
   CONSTRAINT ck_missing_observations_obs CHECK (observation IN ('present', 'absent', 'blocked')),
   CONSTRAINT ck_missing_observations_state CHECK (state_after IN ('present', 'missing_once',
      'missing', 'removed')),
   CONSTRAINT ck_missing_observations_key CHECK (char_length(item_key) BETWEEN 1 AND 300)
);
CREATE INDEX ix_missing_observations_item ON missing_observations (tenant_id, account_id, item_key, id);
ALTER TABLE missing_observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE missing_observations FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON missing_observations TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON missing_observations TO app_rw;
GRANT USAGE ON SEQUENCE missing_observations_id_seq TO app_rw;
