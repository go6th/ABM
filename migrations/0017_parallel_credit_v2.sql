-- Migration 0017: parallel execution and credit manager v2 (expand only; no existing column is
-- dropped or altered). Spec docs/spend/credit-manager-v2.md. down_revision 0016.
--
-- Insert-only (INSERT and SELECT for app_rw, no UPDATE, no DELETE): work_units, work_unit_events,
-- spend_limit_events, notification_outbox, notification_deliveries, prepulled_intents,
-- prepulled_intent_events, provider_limit_events.
-- Mutable and documented as NOT a ledger (scheduling state only, never money or a client record):
--   provider_limits   one row per provider: the adaptive limit and its ceiling (no tenant column;
--                     every tenant's calls share a provider's slots)
--   provider_leases   one row per granted slot; released or expired rows are kept (released_at)
--   provider_waiters  the live queue of requesters per provider (rows leave when granted or dead)
-- The three are written ONLY by the governor_* definer functions, which run as the
-- NOLOGIN role governor_definer: it has a policy that sees every tenant's leases and waiters (the
-- limit is per provider across all runs, D-43) and returns only a lease id, a boolean or a count,
-- never another tenant's row. app_rw reads its own tenant's leases through RLS and writes nothing
-- directly. Forced RLS TO app_rw on every tenant table.
-- account_runs gains awaiting_kind: why a run waits in AWAITING_APPROVAL ('approval' for the
-- first cap, 'cap' for a pause instead of fail, D-45). The state vocabulary does not grow.
-- Rollback: 0017_parallel_credit_v2.down.sql.

ALTER TABLE account_runs
   ADD COLUMN awaiting_kind text,
   ADD CONSTRAINT ck_account_runs_awaiting_kind
      CHECK (awaiting_kind IS NULL OR awaiting_kind IN ('approval', 'cap'));

-- ------------------------------------------------------------------ units and their events (P13)
CREATE TABLE work_units (
   id               bigserial NOT NULL,
   tenant_id        uuid NOT NULL DEFAULT app_tenant(),
   run_id           uuid NOT NULL,
   unit_key         text NOT NULL,
   unit_type        text NOT NULL,
   body             text NOT NULL,
   lane             text,
   shard            text,
   depends_on       text[] NOT NULL DEFAULT '{}',
   soft_depends_on  text[] NOT NULL DEFAULT '{}',
   routes           text[] NOT NULL DEFAULT '{}',
   priority         integer NOT NULL DEFAULT 0,
   est_cost_micros  bigint,
   plan_hash        text NOT NULL,
   created_at       timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_work_units PRIMARY KEY (id),
   CONSTRAINT fk_work_units_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_work_units_run_key UNIQUE (run_id, unit_key),
   -- the composite key children reference: an event can only belong to its own tenant's unit
   CONSTRAINT uq_work_units_tenant_run_key UNIQUE (tenant_id, run_id, unit_key),
   CONSTRAINT ck_work_units_key CHECK (length(unit_key) BETWEEN 1 AND 300),
   CONSTRAINT ck_work_units_cost CHECK (est_cost_micros IS NULL OR est_cost_micros >= 0),
   CONSTRAINT ck_work_units_plan_hash CHECK (plan_hash ~ '^[0-9a-f]{64}$')
);
ALTER TABLE work_units ENABLE ROW LEVEL SECURITY;
ALTER TABLE work_units FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON work_units TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON work_units TO app_rw;
GRANT USAGE ON SEQUENCE work_units_id_seq TO app_rw;

CREATE TABLE work_unit_events (
   id              bigserial NOT NULL,
   tenant_id       uuid NOT NULL DEFAULT app_tenant(),
   run_id          uuid NOT NULL,
   unit_key        text NOT NULL,
   event           text NOT NULL,
   at              timestamptz NOT NULL,
   reason          text,
   provider_code   text,
   lease_wait_s    numeric(12,3) NOT NULL DEFAULT 0,
   error_class     text,
   result_json     jsonb,
   worker          text,
   created_at      timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_work_unit_events PRIMARY KEY (id),
   CONSTRAINT fk_work_unit_events_unit FOREIGN KEY (tenant_id, run_id, unit_key)
      REFERENCES work_units (tenant_id, run_id, unit_key),
   CONSTRAINT ck_work_unit_events_event CHECK (event IN ('queued', 'started', 'finished', 'failed',
      'failed_terminal', 'blocked', 'cancelled', 'requeued', 'cap_denied', 'held')),
   CONSTRAINT ck_work_unit_events_wait CHECK (lease_wait_s >= 0),
   -- results are small references (the 64 KiB checkpoint rule), never text or payloads
   CONSTRAINT ck_work_unit_events_result_size
      CHECK (result_json IS NULL OR octet_length(result_json::text) <= 65536)
);
ALTER TABLE work_unit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE work_unit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON work_unit_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON work_unit_events TO app_rw;
GRANT USAGE ON SEQUENCE work_unit_events_id_seq TO app_rw;
CREATE INDEX ix_work_unit_events_run ON work_unit_events (run_id, id);
-- a terminal event is written once per unit (duplicate deliveries insert nothing)
CREATE UNIQUE INDEX uq_work_unit_events_terminal ON work_unit_events (run_id, unit_key)
   WHERE event IN ('finished', 'failed_terminal', 'blocked');

-- ------------------------------------------------------------------ governor (D-43, P4 to P6)
DO $$
BEGIN
   IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'governor_definer') THEN
      CREATE ROLE governor_definer NOLOGIN NOINHERIT;
   END IF;
END
$$;

CREATE TABLE provider_limits (
   provider_code        text NOT NULL,
   current_limit        integer NOT NULL,
   ceiling              integer NOT NULL,
   floor_limit          integer NOT NULL DEFAULT 1,
   last_change_at       timestamptz,
   last_rate_limited_at timestamptz,
   CONSTRAINT pk_provider_limits PRIMARY KEY (provider_code),
   CONSTRAINT ck_provider_limits_bounds
      CHECK (floor_limit >= 1 AND floor_limit <= current_limit AND current_limit <= ceiling)
);
GRANT SELECT ON provider_limits TO app_rw;
GRANT SELECT, INSERT, UPDATE ON provider_limits TO governor_definer;

CREATE TABLE provider_limit_events (
   id            bigserial NOT NULL,
   provider_code text NOT NULL,
   event         text NOT NULL,
   old_limit     integer,
   new_limit     integer NOT NULL,
   reason        text,
   at            timestamptz NOT NULL,
   CONSTRAINT pk_provider_limit_events PRIMARY KEY (id),
   CONSTRAINT ck_provider_limit_events_event CHECK (event IN ('configured', 'decrease', 'increase'))
);
GRANT SELECT ON provider_limit_events TO app_rw;
GRANT SELECT, INSERT ON provider_limit_events TO governor_definer;
GRANT USAGE ON SEQUENCE provider_limit_events_id_seq TO governor_definer;

CREATE TABLE provider_leases (
   id             uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   run_id         uuid NOT NULL,
   provider_code  text NOT NULL,
   holder         text NOT NULL,
   priority       integer NOT NULL DEFAULT 0,
   acquired_at    timestamptz NOT NULL,
   expires_at     timestamptz NOT NULL,
   heartbeat_at   timestamptz NOT NULL,
   released_at    timestamptz,
   release_reason text,
   CONSTRAINT pk_provider_leases PRIMARY KEY (id),
   CONSTRAINT fk_provider_leases_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_provider_leases_release CHECK (release_reason IS NULL OR release_reason IN
      ('released', 'expired'))
);
ALTER TABLE provider_leases ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_leases FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON provider_leases TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
CREATE POLICY governor_all ON provider_leases TO governor_definer USING (true) WITH CHECK (true);
GRANT SELECT ON provider_leases TO app_rw;
GRANT SELECT, INSERT, UPDATE ON provider_leases TO governor_definer;
CREATE INDEX ix_provider_leases_active ON provider_leases (provider_code, expires_at)
   WHERE released_at IS NULL;

CREATE TABLE provider_waiters (
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   run_id        uuid NOT NULL,
   provider_code text NOT NULL,
   holder        text NOT NULL,
   priority      integer NOT NULL DEFAULT 0,
   since         timestamptz NOT NULL,
   seen_at       timestamptz NOT NULL,
   CONSTRAINT pk_provider_waiters PRIMARY KEY (provider_code, holder),
   CONSTRAINT fk_provider_waiters_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id)
);
ALTER TABLE provider_waiters ENABLE ROW LEVEL SECURITY;
ALTER TABLE provider_waiters FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON provider_waiters TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
CREATE POLICY governor_all ON provider_waiters TO governor_definer USING (true) WITH CHECK (true);
GRANT SELECT ON provider_waiters TO app_rw;
GRANT SELECT, INSERT, UPDATE, DELETE ON provider_waiters TO governor_definer;

-- reviewed: SECURITY DEFINER (owner governor_definer, NOLOGIN): creates or updates one provider's
-- limit row (ceiling and floor from config; the current limit is clamped into the new bounds and
-- starts at the floor); returns nothing; search_path pinned; EXECUTE for app_rw only.
CREATE FUNCTION governor_configure(p_provider text, p_ceiling integer, p_floor integer,
                                   p_now timestamptz)
   RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
   v_old integer;
BEGIN
   IF p_floor < 1 OR p_ceiling < p_floor THEN
      RAISE EXCEPTION 'governor: invalid bounds';
   END IF;
   SELECT current_limit INTO v_old FROM provider_limits WHERE provider_code = p_provider FOR UPDATE;
   IF v_old IS NULL THEN
      INSERT INTO provider_limits (provider_code, current_limit, ceiling, floor_limit)
      VALUES (p_provider, p_floor, p_ceiling, p_floor) ON CONFLICT (provider_code) DO NOTHING;
      INSERT INTO provider_limit_events (provider_code, event, old_limit, new_limit, reason, at)
      VALUES (p_provider, 'configured', NULL, p_floor, 'created at the floor', p_now);
   ELSE
      UPDATE provider_limits
         SET ceiling = p_ceiling, floor_limit = p_floor,
             current_limit = LEAST(GREATEST(current_limit, p_floor), p_ceiling)
       WHERE provider_code = p_provider
         AND (ceiling <> p_ceiling OR floor_limit <> p_floor);
      IF FOUND THEN
         INSERT INTO provider_limit_events (provider_code, event, old_limit, new_limit, reason, at)
         SELECT p_provider, 'configured', v_old, current_limit, 'bounds changed', p_now
           FROM provider_limits WHERE provider_code = p_provider;
      END IF;
   END IF;
END
$$;

-- One acquisition attempt for the calling tenant (app_tenant(), never an argument). Counts every
-- tenant's active leases and live waiters of one provider to apply the limit and the fairness rule
-- of parallel.governor.choose. The provider row lock serialises acquisitions across all workers.
-- reviewed: SECURITY DEFINER (owner governor_definer, NOLOGIN) returns ONLY the new lease id or
-- NULL; no other tenant's data leaves the function; search_path pinned.
CREATE FUNCTION governor_acquire(p_provider text, p_run uuid, p_holder text, p_priority integer,
   p_now timestamptz, p_ttl_s double precision, p_waiter_ttl_s double precision,
   p_run_share integer, p_tenant_share integer, p_aging_s double precision, p_ceiling integer,
   p_floor integer) RETURNS uuid LANGUAGE plpgsql SECURITY DEFINER
   SET search_path = public, pg_temp AS $$
DECLARE
   v_tenant uuid := app_tenant();
   v_limit integer;
   v_active integer;
   v_chosen text;
   v_id uuid;
BEGIN
   IF v_tenant IS NULL THEN
      RAISE EXCEPTION 'governor: no tenant context';
   END IF;
   INSERT INTO provider_limits (provider_code, current_limit, ceiling, floor_limit)
   VALUES (p_provider, p_floor, p_ceiling, p_floor) ON CONFLICT (provider_code) DO NOTHING;
   SELECT current_limit INTO v_limit FROM provider_limits
    WHERE provider_code = p_provider FOR UPDATE;
   -- 1. reclaim expired leases (kept, marked) and drop dead waiters
   UPDATE provider_leases SET released_at = expires_at, release_reason = 'expired'
    WHERE provider_code = p_provider AND released_at IS NULL AND expires_at <= p_now;
   DELETE FROM provider_waiters
    WHERE provider_code = p_provider AND seen_at <= p_now - make_interval(secs => p_waiter_ttl_s);
   -- the requester joins (or refreshes its place in) the queue; its age is preserved
   INSERT INTO provider_waiters (tenant_id, run_id, provider_code, holder, priority, since, seen_at)
   VALUES (v_tenant, p_run, p_provider, p_holder, p_priority, p_now, p_now)
   ON CONFLICT (provider_code, holder)
   DO UPDATE SET seen_at = EXCLUDED.seen_at, priority = EXCLUDED.priority;
   -- 2. no grant at or above the limit
   SELECT count(*) INTO v_active FROM provider_leases
    WHERE provider_code = p_provider AND released_at IS NULL;
   IF v_active >= v_limit THEN
      RETURN NULL;
   END IF;
   -- 3 and 4. eligible waiters under the run and tenant shares; fairness order
   WITH act AS (
      SELECT tenant_id, run_id FROM provider_leases
       WHERE provider_code = p_provider AND released_at IS NULL
   ), by_run AS (
      SELECT run_id, count(*) AS n FROM act GROUP BY run_id
   ), by_tenant AS (
      SELECT tenant_id, count(*) AS n FROM act GROUP BY tenant_id
   )
   SELECT w.holder INTO v_chosen
     FROM provider_waiters w
     LEFT JOIN by_run r ON r.run_id = w.run_id
     LEFT JOIN by_tenant t ON t.tenant_id = w.tenant_id
    WHERE w.provider_code = p_provider
      AND COALESCE(r.n, 0) < GREATEST(1, v_limit * p_run_share / 100)
      AND COALESCE(t.n, 0) < GREATEST(1, v_limit * p_tenant_share / 100)
    ORDER BY COALESCE(t.n, 0) ASC,
             (w.priority + floor(GREATEST(0, extract(epoch FROM (p_now - w.since))) / p_aging_s))
                DESC,
             w.since ASC,
             w.holder ASC
    LIMIT 1;
   IF v_chosen IS DISTINCT FROM p_holder THEN
      RETURN NULL;
   END IF;
   INSERT INTO provider_leases (tenant_id, run_id, provider_code, holder, priority, acquired_at,
                                expires_at, heartbeat_at)
   VALUES (v_tenant, p_run, p_provider, p_holder, p_priority, p_now,
           p_now + make_interval(secs => p_ttl_s), p_now)
   RETURNING id INTO v_id;
   DELETE FROM provider_waiters WHERE provider_code = p_provider AND holder = p_holder;
   RETURN v_id;
END
$$;

-- reviewed: SECURITY DEFINER (owner governor_definer, NOLOGIN): renews the calling tenant's own
-- unexpired lease; returns whether it is still held. search_path pinned.
CREATE FUNCTION governor_heartbeat(p_lease uuid, p_now timestamptz, p_ttl_s double precision)
   RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
   UPDATE provider_leases
      SET expires_at = p_now + make_interval(secs => p_ttl_s), heartbeat_at = p_now
    WHERE id = p_lease AND tenant_id = app_tenant() AND released_at IS NULL
      AND expires_at > p_now;
   RETURN FOUND;
END
$$;

-- reviewed: SECURITY DEFINER (owner governor_definer, NOLOGIN): releases the calling tenant's own
-- lease (kept as a row with released_at). search_path pinned.
CREATE FUNCTION governor_release(p_lease uuid, p_now timestamptz)
   RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
   UPDATE provider_leases SET released_at = p_now, release_reason = 'released'
    WHERE id = p_lease AND tenant_id = app_tenant() AND released_at IS NULL;
END
$$;

-- reviewed: SECURITY DEFINER (owner governor_definer, NOLOGIN): one AIMD step for a provider
-- (parallel.governor.aimd): halve on rate_limited (floor), plus one after a clean window (ceiling),
-- at most one change per window; records the change. Takes no tenant input and returns nothing.
CREATE FUNCTION governor_signal(p_provider text, p_kind text, p_now timestamptz,
                                p_window_s double precision)
   RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE
   v provider_limits%ROWTYPE;
   v_window interval := make_interval(secs => p_window_s);
   v_new integer;
   v_recent boolean;
BEGIN
   IF p_kind NOT IN ('rate_limited', 'clean') THEN
      RAISE EXCEPTION 'governor: unknown signal';
   END IF;
   SELECT * INTO v FROM provider_limits WHERE provider_code = p_provider FOR UPDATE;
   IF NOT FOUND THEN
      RETURN;
   END IF;
   v_recent := v.last_change_at IS NOT NULL AND p_now - v.last_change_at < v_window;
   IF p_kind = 'rate_limited' THEN
      UPDATE provider_limits SET last_rate_limited_at = p_now WHERE provider_code = p_provider;
      v_new := GREATEST(v.floor_limit, v.current_limit / 2);
      IF v_recent OR v_new >= v.current_limit THEN
         RETURN;
      END IF;
      UPDATE provider_limits SET current_limit = v_new, last_change_at = p_now
       WHERE provider_code = p_provider;
      INSERT INTO provider_limit_events (provider_code, event, old_limit, new_limit, reason, at)
      VALUES (p_provider, 'decrease', v.current_limit, v_new, 'rate_limited', p_now);
      RETURN;
   END IF;
   IF v_recent OR v.current_limit >= v.ceiling
      OR (v.last_rate_limited_at IS NOT NULL AND p_now - v.last_rate_limited_at < v_window) THEN
      RETURN;
   END IF;
   IF v.last_change_at IS NULL AND v.last_rate_limited_at IS NULL THEN
      UPDATE provider_limits SET last_change_at = p_now WHERE provider_code = p_provider;
      RETURN;
   END IF;
   UPDATE provider_limits SET current_limit = v.current_limit + 1, last_change_at = p_now
    WHERE provider_code = p_provider;
   INSERT INTO provider_limit_events (provider_code, event, old_limit, new_limit, reason, at)
   VALUES (p_provider, 'increase', v.current_limit, v.current_limit + 1, 'clean_window', p_now);
END
$$;

-- reviewed: SECURITY DEFINER (owner governor_definer, NOLOGIN): the number of active leases of a
-- provider across all tenants (a count only), for the invariant monitor and the operator view.
CREATE FUNCTION governor_active(p_provider text, p_now timestamptz)
   RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
   SELECT count(*)::integer FROM provider_leases
    WHERE provider_code = p_provider AND released_at IS NULL AND expires_at > p_now
$$;

ALTER FUNCTION governor_configure(text, integer, integer, timestamptz) OWNER TO governor_definer;
ALTER FUNCTION governor_acquire(text, uuid, text, integer, timestamptz, double precision,
   double precision, integer, integer, double precision, integer, integer) OWNER TO governor_definer;
ALTER FUNCTION governor_heartbeat(uuid, timestamptz, double precision) OWNER TO governor_definer;
ALTER FUNCTION governor_release(uuid, timestamptz) OWNER TO governor_definer;
ALTER FUNCTION governor_signal(text, text, timestamptz, double precision) OWNER TO governor_definer;
ALTER FUNCTION governor_active(text, timestamptz) OWNER TO governor_definer;
REVOKE ALL ON FUNCTION governor_configure(text, integer, integer, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION governor_acquire(text, uuid, text, integer, timestamptz, double precision,
   double precision, integer, integer, double precision, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION governor_heartbeat(uuid, timestamptz, double precision) FROM PUBLIC;
REVOKE ALL ON FUNCTION governor_release(uuid, timestamptz) FROM PUBLIC;
REVOKE ALL ON FUNCTION governor_signal(text, text, timestamptz, double precision) FROM PUBLIC;
REVOKE ALL ON FUNCTION governor_active(text, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION governor_configure(text, integer, integer, timestamptz) TO app_rw;
GRANT EXECUTE ON FUNCTION governor_acquire(text, uuid, text, integer, timestamptz,
   double precision, double precision, integer, integer, double precision, integer, integer)
   TO app_rw;
GRANT EXECUTE ON FUNCTION governor_heartbeat(uuid, timestamptz, double precision) TO app_rw;
GRANT EXECUTE ON FUNCTION governor_release(uuid, timestamptz) TO app_rw;
GRANT EXECUTE ON FUNCTION governor_signal(text, text, timestamptz, double precision) TO app_rw;
GRANT EXECUTE ON FUNCTION governor_active(text, timestamptz) TO app_rw;

-- ------------------------------------------------------------------ credit manager v2 (M1 to M12)
CREATE TABLE spend_limit_events (
   id            bigserial NOT NULL,
   tenant_id     uuid NOT NULL DEFAULT app_tenant(),
   run_id        uuid NOT NULL,
   cap_version   integer NOT NULL,
   event         text NOT NULL,
   request_id    uuid,
   detail_json   jsonb NOT NULL DEFAULT '{}'::jsonb,
   principal     text,
   at            timestamptz NOT NULL,
   CONSTRAINT pk_spend_limit_events PRIMARY KEY (id),
   CONSTRAINT fk_spend_limit_events_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT ck_spend_limit_events_event CHECK (event IN ('soft_crossed', 'pause_opened',
      'approved', 'declined', 'timed_out', 'late_approval_refused', 'exhausted', 'alternate_used',
      'upload_requested', 'upload_accepted', 'upload_rejected')),
   CONSTRAINT ck_spend_limit_events_version CHECK (cap_version >= 1),
   CONSTRAINT ck_spend_limit_events_request CHECK (event NOT IN ('pause_opened', 'approved',
      'declined', 'timed_out') OR request_id IS NOT NULL)
);
ALTER TABLE spend_limit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE spend_limit_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON spend_limit_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON spend_limit_events TO app_rw;
GRANT USAGE ON SEQUENCE spend_limit_events_id_seq TO app_rw;
-- M1: one soft crossing per cap version; M3: one pause request per cap version; M5: one decision
-- per request (approve, decline and timeout race; exactly one wins)
CREATE UNIQUE INDEX uq_spend_limit_soft ON spend_limit_events (run_id, cap_version)
   WHERE event = 'soft_crossed';
CREATE UNIQUE INDEX uq_spend_limit_pause ON spend_limit_events (run_id, cap_version)
   WHERE event = 'pause_opened';
CREATE UNIQUE INDEX uq_spend_limit_decision ON spend_limit_events (request_id)
   WHERE event IN ('approved', 'declined', 'timed_out');

CREATE TABLE notification_outbox (
   id           uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   run_id       uuid,
   dedupe_key   text NOT NULL,
   audience     text NOT NULL,
   template     text NOT NULL,
   params_json  jsonb NOT NULL DEFAULT '{}'::jsonb,
   created_at   timestamptz NOT NULL,
   CONSTRAINT pk_notification_outbox PRIMARY KEY (id),
   CONSTRAINT fk_notification_outbox_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_notification_outbox_dedupe UNIQUE (tenant_id, dedupe_key),
   CONSTRAINT uq_notification_outbox_tenant_id UNIQUE (tenant_id, id),
   CONSTRAINT ck_notification_outbox_audience CHECK (audience IN ('tenant', 'operator')),
   CONSTRAINT ck_notification_outbox_params
      CHECK (octet_length(params_json::text) <= 4096 AND jsonb_typeof(params_json) = 'object')
);
ALTER TABLE notification_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_outbox FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON notification_outbox TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON notification_outbox TO app_rw;

CREATE TABLE notification_deliveries (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   outbox_id    uuid NOT NULL,
   attempt      integer NOT NULL,
   outcome      text NOT NULL,
   error_class  text,
   at           timestamptz NOT NULL,
   CONSTRAINT pk_notification_deliveries PRIMARY KEY (id),
   CONSTRAINT fk_notification_deliveries_outbox FOREIGN KEY (tenant_id, outbox_id)
      REFERENCES notification_outbox (tenant_id, id),
   CONSTRAINT uq_notification_deliveries_attempt UNIQUE (outbox_id, attempt),
   CONSTRAINT ck_notification_deliveries_outcome CHECK (outcome IN ('delivered', 'failed',
      'gave_up')),
   CONSTRAINT ck_notification_deliveries_attempt CHECK (attempt >= 1)
);
ALTER TABLE notification_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_deliveries FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON notification_deliveries TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON notification_deliveries TO app_rw;
GRANT USAGE ON SEQUENCE notification_deliveries_id_seq TO app_rw;
-- one terminal delivery outcome per message (idempotent consumer)
CREATE UNIQUE INDEX uq_notification_deliveries_final ON notification_deliveries (outbox_id)
   WHERE outcome IN ('delivered', 'gave_up');

CREATE TABLE prepulled_intents (
   id              uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id       uuid NOT NULL DEFAULT app_tenant(),
   run_id          uuid NOT NULL,
   provider_code   text NOT NULL,
   operation       text NOT NULL,
   spec_json       jsonb NOT NULL,
   spec_hash       text NOT NULL,
   quarantine_key  text NOT NULL,
   created_by      text NOT NULL,
   expires_at      timestamptz NOT NULL,
   created_at      timestamptz NOT NULL,
   CONSTRAINT pk_prepulled_intents PRIMARY KEY (id),
   CONSTRAINT fk_prepulled_intents_tenant FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT uq_prepulled_intents_spec UNIQUE (run_id, spec_hash),
   CONSTRAINT uq_prepulled_intents_tenant_id UNIQUE (tenant_id, id),
   CONSTRAINT ck_prepulled_intents_hash CHECK (spec_hash ~ '^[0-9a-f]{64}$'),
   CONSTRAINT ck_prepulled_intents_key CHECK (quarantine_key ~ '^quarantine/')
);
ALTER TABLE prepulled_intents ENABLE ROW LEVEL SECURITY;
ALTER TABLE prepulled_intents FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON prepulled_intents TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON prepulled_intents TO app_rw;

CREATE TABLE prepulled_intent_events (
   id           bigserial NOT NULL,
   tenant_id    uuid NOT NULL DEFAULT app_tenant(),
   intent_id    uuid NOT NULL,
   event        text NOT NULL,
   reason_code  text,
   sha256       text,
   principal    text,
   detail_json  jsonb NOT NULL DEFAULT '{}'::jsonb,
   at           timestamptz NOT NULL,
   CONSTRAINT pk_prepulled_intent_events PRIMARY KEY (id),
   CONSTRAINT fk_prepulled_intent_events_intent FOREIGN KEY (tenant_id, intent_id)
      REFERENCES prepulled_intents (tenant_id, id),
   CONSTRAINT ck_prepulled_intent_events_event CHECK (event IN ('created', 'completed',
      'scan_clean', 'scan_failed', 'scan_unavailable', 'accepted', 'rejected')),
   CONSTRAINT ck_prepulled_intent_events_sha CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$')
);
ALTER TABLE prepulled_intent_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE prepulled_intent_events FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON prepulled_intent_events TO app_rw
   USING (tenant_id = (SELECT app_tenant())) WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT ON prepulled_intent_events TO app_rw;
GRANT USAGE ON SEQUENCE prepulled_intent_events_id_seq TO app_rw;
-- one verdict per intent (accepted or rejected); duplicate file hashes are refused in code (M8)
CREATE UNIQUE INDEX uq_prepulled_intent_verdict ON prepulled_intent_events (intent_id)
   WHERE event IN ('accepted', 'rejected');
