-- The nightly isolation check (admin console safeguard): the two dedicated check clients it uses.
-- The probe client reads; the canary client holds a known canary account. Neither is a real
-- client: the console and the platform jobs leave them out. A platform row (no tenant data).
CREATE TABLE isolation_check_pair (
   id             smallint NOT NULL DEFAULT 1,
   probe_client   uuid NOT NULL REFERENCES tenants (id),
   canary_client  uuid NOT NULL REFERENCES tenants (id),
   canary         text NOT NULL,
   created_at     timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_isolation_check_pair PRIMARY KEY (id),
   CONSTRAINT ck_isolation_check_pair_one CHECK (id = 1),
   CONSTRAINT ck_isolation_check_pair_two CHECK (probe_client <> canary_client),
   CONSTRAINT ck_isolation_check_pair_canary CHECK (canary ~ '^[a-z0-9-]{8,64}$')
);
GRANT SELECT, INSERT ON isolation_check_pair TO app_rw;
