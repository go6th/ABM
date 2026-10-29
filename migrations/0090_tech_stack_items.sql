-- Organisation tech stack (2026-10-29): the stack the organisation lookup returns for the account
-- (business function, category, technology and how many job postings used it) was pulled every
-- run and never stored, so the Tech Stack and Teams workbook had nothing to print on its
-- "Confirmed stack" sheet. One row per (run, business function, category, technology),
-- insert-only; the postings count is the feed's own ``job_post_used_count`` and ``position`` is
-- the item's place in the feed's own order (the sheet prints the stack in that order). Owned like the
-- account (owner_user_id filled from the session user), row level security forced.
CREATE TABLE tech_stack_items (
   id                uuid NOT NULL DEFAULT gen_random_uuid(),
   tenant_id         uuid NOT NULL DEFAULT app_tenant(),
   owner_user_id     text NOT NULL DEFAULT app_user(),
   account_id        uuid NOT NULL,
   run_id            uuid NOT NULL,
   business_function text NOT NULL,
   category          text NOT NULL,
   technology        text NOT NULL,
   postings          integer NOT NULL DEFAULT 0,
   position          integer NOT NULL DEFAULT 0,
   source_ref        text,
   created_at        timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_tech_stack_items PRIMARY KEY (id),
   CONSTRAINT fk_tech_stack_items_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT fk_tech_stack_items_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT uq_tech_stack_items_run_item
      UNIQUE (tenant_id, run_id, business_function, category, technology),
   CONSTRAINT ck_tech_stack_items_postings CHECK (postings >= 0),
   CONSTRAINT ck_tech_stack_items_position CHECK (position >= 0),
   CONSTRAINT ck_tech_stack_items_technology CHECK (btrim(technology) <> '')
);
COMMENT ON TABLE tech_stack_items IS 'The organisation tech stack of a run: technology by business function and category with the postings count the lookup reports.';
CREATE INDEX ix_tech_stack_items_run ON tech_stack_items (tenant_id, account_id, run_id);
CREATE INDEX ix_tech_stack_items_tenant_owner ON tech_stack_items (tenant_id, owner_user_id);
ALTER TABLE tech_stack_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE tech_stack_items FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_owner_isolation ON tech_stack_items TO app_rw
   USING (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'))
   WITH CHECK (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'));
GRANT SELECT, INSERT ON tech_stack_items TO app_rw;   -- insert-only: a pulled stack is never rewritten
