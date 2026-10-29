-- Link archive (2026-10-27): every URL a research stores (a profile, a posting, a page a search
-- found or read, a detection's evidence page) is kept with the record body it came from, when it
-- was captured and the original address, so a row of the delivered files or the web tables can
-- open the archived copy once the live link dies. Insert-only: one row per captured copy; the
-- newest copy of an address is the one shown. The body lives in the payload store under a
-- neutral key (``payload_ref``): a Phase 1 source item as the pull stored it (``format`` json, a
-- JSON object with ``url`` and ``text``), a posting's stored text (format text) or a small JSON
-- body the archive wrote for a profile or a detection. ``source_ref`` keeps the connector ledger
-- reference of the raw provider body for traceability; it is never served.
CREATE TABLE link_archive (
   id             bigserial NOT NULL,
   tenant_id      uuid NOT NULL DEFAULT app_tenant(),
   owner_user_id  text NOT NULL DEFAULT app_user(),
   account_id     uuid NOT NULL,
   run_id         uuid,
   url            text NOT NULL,
   url_normalized text NOT NULL,
   url_hash       char(64) NOT NULL,
   kind           text NOT NULL,
   payload_ref    text NOT NULL,
   format         text NOT NULL DEFAULT 'json',
   locator        text,
   title          text,
   source_ref     text,
   archived_at    timestamptz NOT NULL,
   created_at     timestamptz NOT NULL DEFAULT now(),
   CONSTRAINT pk_link_archive PRIMARY KEY (id),
   CONSTRAINT fk_link_archive_tenant_id_tenants FOREIGN KEY (tenant_id) REFERENCES tenants (id),
   CONSTRAINT fk_link_archive_tenant_id_account_id_accounts
      FOREIGN KEY (tenant_id, account_id) REFERENCES accounts (tenant_id, id),
   CONSTRAINT uq_link_archive_copy UNIQUE (tenant_id, account_id, url_hash, payload_ref),
   CONSTRAINT ck_link_archive_url_is_http CHECK (url ~* '^https?://\S+$'),
   CONSTRAINT ck_link_archive_url_normalized_is_http CHECK (url_normalized ~ '^https?://\S+$'),
   -- the database proves the lookup key: url_hash is the sha256 of url_normalized
   CONSTRAINT ck_link_archive_url_hash_matches CHECK (
      url_hash = encode(sha256(convert_to(url_normalized, 'UTF8')), 'hex')
   ),
   CONSTRAINT ck_link_archive_kind CHECK (kind IN ('profile', 'posting', 'page', 'feed_item', 'detection')),
   CONSTRAINT ck_link_archive_format CHECK (format IN ('json', 'text')),
   CONSTRAINT ck_link_archive_payload_ref CHECK (btrim(payload_ref) <> ''),
   CONSTRAINT ck_link_archive_title_length CHECK (title IS NULL OR char_length(title) <= 300)
);
COMMENT ON TABLE link_archive IS 'Archived copies of every address a research stored: original URL, the stored body, when it was captured.';
COMMENT ON COLUMN link_archive.payload_ref IS 'Neutral payload key of the archived body (never a provider body).';
COMMENT ON COLUMN link_archive.source_ref IS 'Connector ledger reference of the raw provider body the copy came from; internal only.';
CREATE INDEX ix_link_archive_lookup ON link_archive (tenant_id, account_id, url_hash, archived_at DESC);
CREATE INDEX ix_link_archive_kind ON link_archive (tenant_id, account_id, kind, archived_at DESC);
CREATE INDEX ix_link_archive_tenant_owner ON link_archive (tenant_id, owner_user_id);
ALTER TABLE link_archive ENABLE ROW LEVEL SECURITY;
ALTER TABLE link_archive FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_owner_isolation ON link_archive TO app_rw
   USING (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'))
   WITH CHECK (tenant_id = (SELECT app_tenant())
      AND (owner_user_id = (SELECT app_user()) OR (SELECT app_scope()) = 'system'));
GRANT SELECT, INSERT ON link_archive TO app_rw;   -- insert-only: a copy is never rewritten
GRANT USAGE ON SEQUENCE link_archive_id_seq TO app_rw;
