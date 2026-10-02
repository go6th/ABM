-- Data layer v1, step 10: per-account source code allocator.
-- One row per account. The allocator locks the row (SELECT ... FOR UPDATE), takes next_value
-- for the new source code and increments it in the same transaction, so codes are gapless
-- and two workers never receive the same code.

CREATE TABLE account_source_counters (
   account_id uuid NOT NULL,
   tenant_id  uuid NOT NULL DEFAULT app_tenant(),
   next_value integer NOT NULL DEFAULT 1,
   created_at timestamptz NOT NULL DEFAULT now(),
   updated_at timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_account_source_counters PRIMARY KEY (account_id),
   CONSTRAINT fk_account_source_counters_tenant_id_tenants
      FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT fk_account_source_counters_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT ck_account_source_counters_next_value_positive CHECK (next_value >= 1)
);

-- Codes are never renumbered: the counter only moves forward.
CREATE FUNCTION account_source_counters_forward_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
   IF NEW.next_value < OLD.next_value OR NEW.account_id <> OLD.account_id
      OR NEW.tenant_id <> OLD.tenant_id THEN
      RAISE EXCEPTION 'account_source_counters: counter for account % cannot move backwards',
         OLD.account_id USING ERRCODE = 'check_violation';
   END IF;
   RETURN NEW;
END $$;
CREATE TRIGGER account_source_counters_forward_only BEFORE UPDATE ON account_source_counters
   FOR EACH ROW EXECUTE FUNCTION account_source_counters_forward_only();

ALTER TABLE account_source_counters ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_source_counters FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON account_source_counters TO app_rw
   USING      (tenant_id = (SELECT app_tenant()))
   WITH CHECK (tenant_id = (SELECT app_tenant()));
GRANT SELECT, INSERT, UPDATE ON account_source_counters TO app_rw;
